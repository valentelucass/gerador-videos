"""Máquina de estados da automação de animação.

Não há limite artificial de tentativas: falhas transitórias retornam ao último
ponto seguro (selecionar a imagem) e o checkpoint impede repetir sucessos.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .audit import AuditTrail
from .checkpoint import CheckpointStore
from .click_feedback import show_click_target
from .config import Settings
from .retry import RetryManager
from .selectors import (
    ANIMATE_BUTTON, CLOSE_POPUP_BUTTON, CONFIRM_UPLOAD_BUTTON, CREATE_NEW_BUTTON, EDITOR_SIDEBAR_THUMBNAILS, EDIT_IMAGE_TITLE, EDIT_VIDEO_TITLE, NO_PROJECT_YET,
    ERROR_ALERT, MANUAL_ANIMATE_BUTTON, PROCESSING_INDICATOR, PROMPT_TEXTAREA,
    LOGIN_INDICATOR, MEDIA_THUMBNAIL, PLATFORM_ERROR_PAGE, RATE_LIMIT_ALERT, SUCCESS_ALERT, UPLOAD_BUTTON, UPLOAD_DROPZONE, UPLOAD_ERROR_ALERT, UPLOAD_SUCCESS_ALERT,
    first_enabled, first_visible,
    is_visible,
)
from .state_machine import State
from .utils import batches, prompt_text


class RateLimitDetected(Exception):
    pass


class PlatformPageError(RuntimeError):
    """Tela fatal do Vibes que deve ser recarregada, não tratada como sucesso."""


class BrowserSessionClosed(RuntimeError):
    """A janela do robô foi fechada; não existe recuperação automática segura."""


class AnimationWorkflow:
    def __init__(
        self,
        page: Page,
        settings: Settings,
        logger: logging.Logger,
        *,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.page = page
        self.settings = settings
        self.logger = logger
        self.audit = AuditTrail(settings.logs_dir, settings.state_path.parent)
        self.retry = RetryManager(
            logger, settings.retry_initial_delay, settings.retry_max_delay,
            settings.show_click_highlight, settings.click_highlight_duration_ms, self.audit.record,
        )
        self.checkpoint = CheckpointStore(checkpoint_path or settings.state_path)
        self.prompt = prompt_text(settings.prompt_path)
        self.state = State.HOME
        self._media_order: list[Path] = []
        self.audit.record("workflow_initialized", platform_url=settings.platform_url)

    def _set_state(self, state: State, image: Path | None = None) -> None:
        self.state = state
        suffix = f" | imagem: {image.name}" if image else ""
        self.logger.info("Estado: %s%s", state.value, suffix)
        self.audit.record("state_changed", state=state.value, image=image.name if image else None)

    @staticmethod
    def _project_url_from_vibes_url(url: str) -> str:
        """Normaliza uma URL de conteúdo para a página raiz do projeto Vibes."""
        parsed = urlsplit(url)
        marker = "/projects/"
        if marker not in parsed.path:
            return url
        project_id = parsed.path.split(marker, 1)[1].split("/", 1)[0]
        return f"{parsed.scheme}://{parsed.netloc}{marker}{project_id}"

    async def _locator(self, name: str, group, *, recover: bool = True):
        return await self.retry.retry_locator(
            name, lambda: self._wait_for_visible(group, self.settings.timeout_ms), self._recover if recover else None,
        )

    async def _raise_if_platform_error(self) -> None:
        """Detecta a tela fatal do Vibes e agenda o refresh com calma."""
        if not await is_visible(self.page, PLATFORM_ERROR_PAGE):
            return
        error = await first_visible(self.page, PLATFORM_ERROR_PAGE, 1_000)
        message = (await error.inner_text()).strip().replace("\n", " ") or "Something went wrong"
        self.logger.warning(
            "Tela fatal do Vibes detectada: %s. Aguardando %ss antes de atualizar e retomar %s.",
            message, self.settings.platform_error_refresh_delay, self.state.value,
        )
        self.audit.record("platform_error_page_detected", message=message, state=self.state.value, url=self.page.url)
        await asyncio.sleep(self.settings.platform_error_refresh_delay)
        raise PlatformPageError(message)

    async def _wait_for_visible(self, group, timeout_ms: int):
        """Espera um destino sem ignorar a tela fatal intermediária do Vibes."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            await self._raise_if_platform_error()
            remaining_ms = max(250, min(1_000, int((deadline - time.monotonic()) * 1000)))
            try:
                return await first_visible(self.page, group, remaining_ms)
            except PlaywrightTimeoutError:
                continue
        raise PlaywrightTimeoutError("Nenhum seletor alternativo ficou visível dentro do timeout.")

    async def _recover(self, exc: Exception) -> None:
        """Salva evidência e recarrega sem descartar a sessão ou o checkpoint."""
        if self.page.is_closed():
            raise BrowserSessionClosed("A janela do Firefox da automação foi fechada.") from exc
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.audit.record("recovery_started", state=self.state.value, error_type=type(exc).__name__, error=str(exc))
        try:
            screenshot = self.settings.artifacts_dir / f"failure-{stamp}.png"
            html = self.settings.artifacts_dir / f"failure-{stamp}.html"
            await self.page.screenshot(path=str(screenshot), full_page=True)
            html.write_text(await self.page.content(), encoding="utf-8")
            self.audit.record("recovery_artifacts_saved", screenshot=str(screenshot), html=str(html))
        except Exception as artifact_error:
            self.logger.warning("Não foi possível salvar artefato: %s", artifact_error)
        self.logger.info("Refresh para recuperação: %s", exc)
        await self.retry.retry_refresh(self.page)
        self.audit.record("recovery_completed", state=self.state.value)

    async def run(
        self,
        files: list[Path],
        *,
        duplicates: list[Path] | None = None,
        resume_target_url: str | None = None,
    ) -> None:
        if not files:
            raise ValueError("Nenhuma mídia de cena foi recebida da biblioteca do painel.")
        duplicates = duplicates or []
        self.logger.info("%s imagem(ns) encontrada(s).", len(files))
        self.audit.record(
            "run_started", image_count=len(files), images=[image.name for image in files],
            duplicate_count=len(duplicates), duplicate_images=[image.name for image in duplicates],
        )
        response = await self.page.goto(self.settings.platform_url, wait_until="domcontentloaded")
        navigation = {
            "http_status": response.status if response else None,
            "url": self.page.url,
            "title": await self.page.title(),
        }
        self.logger.info("Vibes carregado | status=%s | título=%s | url=%s", navigation["http_status"], navigation["title"], navigation["url"])
        self.audit.record("navigation_completed", **navigation)
        await self._wait_for_authenticated_session()
        if resume_target_url:
            self.logger.info("Retomada explícita solicitada | url=%s", resume_target_url)
            self.audit.record("explicit_resume_requested", url=resume_target_url, image_count=len(files))
            await self.page.goto(resume_target_url, wait_until="domcontentloaded")
            await self._raise_if_platform_error()
            project_url = self._project_url_from_vibes_url(resume_target_url)
            # A URL foi fornecida pelo operador como projeto já carregado. Ela
            # transforma a fase de upload em concluída, mas mantém intactas as
            # marcas de animação já finalizadas.
            self.checkpoint.mark_uploaded_batch(files, project_url)
            pending_uploads: list[Path] = []
            self.logger.info("Upload preservado pela retomada explícita | projeto=%s | mídias=%s", project_url, len(files))
        else:
            project_url = self.checkpoint.project_url_for(files)
            pending_uploads = [image for image in files if not self.checkpoint.is_uploaded(image)]
            uploaded_count = len(files) - len(pending_uploads)
        # Uma execução só pode trabalhar com um projeto Vibes. Se o histórico
        # ficou partido entre projetos diferentes (por exemplo, após apagar um
        # projeto no site), não há um destino seguro para continuar: reinicia
        # o conjunto atual do zero, ainda em grupos de no máximo 12.
        if not resume_target_url and uploaded_count and project_url is None:
            self.logger.warning(
                "Checkpoint inconsistente: %s mídia(s) apontam para projetos diferentes. "
                "Será criado um único projeto e as %s mídia(s) serão reenviadas em lotes de %s.",
                uploaded_count, len(files), self.settings.max_upload,
            )
            self.audit.record("mixed_project_checkpoint_reset", uploaded_count=uploaded_count, image_count=len(files))
            self.checkpoint.clear_project_state(files)
            pending_uploads = list(files)
        if project_url and not resume_target_url:
            self.logger.info(
                "Retomando projeto salvo | projeto=%s | já enviados=%s | restantes=%s",
                project_url, len(files) - len(pending_uploads), len(pending_uploads),
            )
            self.audit.record("upload_resume_detected", project_url=project_url, remaining=len(pending_uploads))
            await self.page.goto(project_url, wait_until="domcontentloaded")
            if await is_visible(self.page, NO_PROJECT_YET):
                self.logger.warning(
                    "O projeto salvo não existe mais no Vibes. Limpando checkpoint e iniciando um novo projeto."
                )
                self.audit.record("saved_project_missing", project_url=project_url)
                self.checkpoint.clear_project_state(files)
                project_url = None
                pending_uploads = list(files)
                await self.page.goto(self.settings.platform_url, wait_until="domcontentloaded")
        elif not resume_target_url:
            self.logger.info("Nenhum upload confirmado no checkpoint; será criado um único projeto novo.")

        for number, batch in enumerate(batches(pending_uploads, self.settings.max_upload), start=1):
            if project_url:
                self.logger.info("Retomando o projeto atual para enviar o grupo %s.", number)
                await self.page.goto(project_url, wait_until="domcontentloaded")
            self.logger.info("Iniciando grupo %s (%s imagem(ns)).", number, len(batch))
            self.audit.record("group_started", group=number, images=[image.name for image in batch])
            project_url = await self._upload_batch(batch, create_project=project_url is None)
            self.logger.info("Grupo %s enviado com sucesso | projeto=%s", number, project_url)
            self.audit.record("group_uploaded", group=number, project_url=project_url, images=[image.name for image in batch])

        self.logger.info("Todos os grupos foram enviados. Iniciando animação imagem por imagem.")
        pending_images = [image for image in files if self.checkpoint.is_uploaded(image) and not self.checkpoint.is_complete(image)]
        self.audit.record("upload_phase_completed", image_count=len(pending_images), project_url=project_url)
        if project_url:
            self.logger.info("Abrindo o projeto com %s imagem(ns) para animar uma por uma.", len(pending_images))
            animation_url = resume_target_url or project_url
            if self.page.url != animation_url:
                await self.page.goto(animation_url, wait_until="domcontentloaded")
            self._media_order = [image for image in files if self.checkpoint.is_uploaded(image)]
            if "/content/" not in animation_url:
                await self._bind_media_ids(self._media_order)
            else:
                self.logger.info("Editor Vibes retomado diretamente; seleção seguirá pelos thumbnails da coluna esquerda.")
        # Uma retomada por ``/content/`` já aponta para o editor. A UI ainda
        # pode estar montando no primeiro instante; usar duas sondagens de
        # 250 ms aqui fazia o fluxo cair no fallback do grid e pegar uma mídia
        # arbitrária do manifesto. Aguarda o editor real antes de iniciar a
        # ordem da coluna esquerda.
        resumed_in_editor = bool(resume_target_url and "/content/" in resume_target_url)
        if resumed_in_editor:
            await self._editor_kind()
            final_failures = await self._animate_sidebar_bottom_up(pending_images)
        elif await is_visible(self.page, EDIT_IMAGE_TITLE) or await is_visible(self.page, EDIT_VIDEO_TITLE):
            final_failures = await self._animate_sidebar_bottom_up(pending_images)
        elif pending_images:
            # A primeira abertura ainda está no grid do projeto. Entramos pelo
            # último item conhecido e, dentro do editor, a coluna esquerda
            # passa a definir a ordem real do restante.
            first_image = pending_images[-1]
            completed_or_skipped = await self._animate_image(first_image)
            remaining = [image for image in pending_images if image != first_image]
            if not completed_or_skipped:
                # A primeira imagem também entra na fila de pendências; ela
                # não pode desaparecer só porque o editor foi aberto pelo grid.
                remaining.append(first_image)
            final_failures = await self._animate_sidebar_bottom_up(remaining)
        else:
            final_failures = []
        self._final_evaluation(files, duplicates, final_failures)

    async def _wait_for_authenticated_session(self) -> None:
        """Preserva uma tela de login para intervenção humana, sem refresh.

        Firefox não pode ser anexado com segurança ao perfil já aberto. Usamos
        uma cópia do perfil para herdar cookies; se a sessão expirar, o
        operador faz login uma vez na janela visível e o fluxo prossegue.
        """
        deadline = time.monotonic() + self.settings.timeout_ms / 1000
        login_reported = False
        while True:
            try:
                await self._raise_if_platform_error()
            except PlatformPageError as exc:
                await self._recover(exc)
                deadline = time.monotonic() + self.settings.timeout_ms / 1000
                continue
            if await is_visible(self.page, CREATE_NEW_BUTTON) or await is_visible(self.page, UPLOAD_BUTTON):
                if login_reported:
                    self._set_state(State.HOME)
                    self.logger.info("Login concluído pelo operador; retomando automação.")
                    self.audit.record("manual_login_completed")
                return
            if await is_visible(self.page, LOGIN_INDICATOR):
                if not login_reported:
                    login_reported = True
                    self._set_state(State.AUTHENTICATION)
                    self.logger.warning("Login solicitado pelo Vibes. Faça login nesta janela; o robô aguardará sem atualizar a página.")
                    self.audit.record("manual_login_required", url=self.page.url)
                await asyncio.sleep(2)
                continue
            if time.monotonic() >= deadline:
                raise PlaywrightTimeoutError("O Vibes não exibiu a área autenticada nem uma tela de login no prazo configurado.")
            await asyncio.sleep(0.5)

    async def _reach_destination(self, *, action_name: str, action_group, destination_name: str, destination_group) -> None:
        """Executa uma navegação de UI somente quando o destino for confirmado.

        Em telas instáveis, um clique sem mudança de interface não é sucesso.
        Após uma recuperação, a operação primeiro verifica se o destino já foi
        alcançado (o clique anterior pode ter sido processado tardiamente) e,
        se não foi, clica novamente com a pausa configurada entre tentativas.
        """
        async def attempt() -> None:
            await self._raise_if_platform_error()
            if await is_visible(self.page, destination_group):
                self.logger.info("Destino já confirmado | %s", destination_name)
                self.audit.record("destination_already_reached", destination=destination_name, url=self.page.url)
                return

            origin_url = self.page.url
            self.logger.info("Transição iniciada | ação=%s | destino=%s | url=%s", action_name, destination_name, origin_url)
            action = await self._wait_for_visible(action_group, self.settings.timeout_ms)
            await self._click_once(action)
            try:
                await self._wait_for_visible(destination_group, self.settings.timeout_ms)
            except Exception as exc:
                self.logger.warning(
                    "Destino não confirmado após %s | esperado=%s | url antes=%s | url atual=%s",
                    action_name, destination_name, origin_url, self.page.url,
                )
                self.audit.record(
                    "destination_not_reached", action=action_name, destination=destination_name,
                    origin_url=origin_url, current_url=self.page.url, error=str(exc),
                )
                raise
            self.logger.info("Destino confirmado | ação=%s | destino=%s | url=%s", action_name, destination_name, self.page.url)
            self.audit.record("destination_reached", action=action_name, destination=destination_name, url=self.page.url)

        await self.retry.retry_until_success(
            f"transição {action_name} → {destination_name}",
            attempt,
            self._recover,
            retry_delay_seconds=self.settings.error_retry_delay,
        )

    async def _upload_batch(self, batch: list[Path], *, create_project: bool) -> str:
        """Envia um grupo inteiro de forma idempotente; nunca retoma no meio."""
        needs_project_creation = create_project

        async def attempt() -> str:
            nonlocal needs_project_creation
            saved_project = self.checkpoint.project_url_for(batch)
            if saved_project and all(self.checkpoint.is_uploaded(image) for image in batch):
                self.logger.info("Grupo já confirmado no checkpoint; não será reenviado | projeto=%s", saved_project)
                self.audit.record("upload_group_skipped_already_confirmed", project_url=saved_project, images=[image.name for image in batch])
                return saved_project
            self.logger.info("Upload do grupo iniciado | arquivos=%s", ", ".join(image.name for image in batch))
            if needs_project_creation and not await is_visible(self.page, UPLOAD_BUTTON):
                self._set_state(State.CREATE)
                await self._reach_destination(
                    action_name="Create new",
                    action_group=CREATE_NEW_BUTTON,
                    destination_name="projeto aberto / Upload media",
                    destination_group=UPLOAD_BUTTON,
                )
            # Só sai do modo de criação depois de verificar que o destino
            # existe. Antes disso o próximo ciclo precisa clicar Create new
            # novamente, não tentar usar um projeto inexistente.
            if needs_project_creation and await is_visible(self.page, UPLOAD_BUTTON):
                needs_project_creation = False
            if not await is_visible(self.page, UPLOAD_BUTTON):
                raise PlaywrightTimeoutError("O botão Upload media não apareceu no projeto atual.")
            self._set_state(State.UPLOAD)
            media_before_upload = await self._media_count()
            expected_media_count = media_before_upload + len(batch)
            self.logger.info(
                "Galeria antes do grupo | cards=%s | esperado após confirmação=%s",
                media_before_upload, expected_media_count,
            )
            await self._reach_destination(
                action_name="Upload media",
                action_group=UPLOAD_BUTTON,
                destination_name="modal de seleção de arquivos",
                destination_group=UPLOAD_DROPZONE,
            )

            selected = await self._choose_upload_files(batch)
            self.logger.info("Arquivos selecionados no modal | esperado=%s | selecionado=%s", len(batch), selected)
            self.audit.record("files_selected_for_upload", images=[image.name for image in batch], selected_count=selected)

            confirm = await first_enabled(self.page, CONFIRM_UPLOAD_BUTTON, self.settings.timeout_ms)
            self.logger.info("Botão Upload habilitado; confirmando o grupo de %s imagem(ns).", len(batch))
            await self._click_once(confirm)
            self._set_state(State.WAIT_UPLOAD)
            await self._wait_for_upload_result()
            # O servidor já aceitou os arquivos. Este checkpoint é gravado
            # antes de qualquer refresh/contagem para que travamentos ou uma
            # nova execução nunca reenviem o mesmo grupo de 12.
            confirmed_project_url = self.page.url
            for image in batch:
                self.checkpoint.mark_uploaded(image, confirmed_project_url)
            self.audit.record(
                "upload_checkpoint_saved",
                project_url=confirmed_project_url,
                images=[image.name for image in batch],
            )
            self.logger.info(
                "Toast confirmado; este grupo não será reenviado. Recarregando projeto e aguardando %s cards na galeria.",
                expected_media_count,
            )
            await self.retry.retry_refresh(self.page)
            visible_count = await self._wait_for_media_count(expected_media_count)
            self.logger.info("Upload confirmado para %s imagem(ns) | cards na galeria=%s.", len(batch), visible_count)
            self.audit.record(
                "upload_confirmed", images=[image.name for image in batch],
                media_before_upload=media_before_upload, media_after_upload=visible_count,
            )
            return confirmed_project_url

        async def recovery(exc: Exception) -> None:
            if isinstance(exc, RateLimitDetected):
                self._set_state(State.RATE_LIMIT)
                self.logger.warning(
                    "Rate limit no upload. Aguardando %ss antes de atualizar e reenviar o grupo.",
                    self.settings.rate_limit_wait,
                )
                self.audit.record("upload_rate_limit_detected", wait_seconds=self.settings.rate_limit_wait)
                await asyncio.sleep(self.settings.rate_limit_wait)
            await self._recover(exc)

        return await self.retry.retry_until_success(
            f"upload completo de {len(batch)} imagens", attempt, recovery,
            retry_delay_seconds=self.settings.error_retry_delay,
        )

    async def _choose_upload_files(self, batch: list[Path]) -> int:
        """Envia arquivos pelo input existente ou pelo seletor dinâmico do Vibes."""
        # O Vibes mantém inputs internos que podem existir fora do modal ou
        # já terem sido desmontados. Nunca usamos o primeiro input da página:
        # o clique na área do modal fornece o FileChooser correto.
        dropzone = await first_visible(self.page, UPLOAD_DROPZONE, self.settings.timeout_ms)
        self.logger.info("Abrindo o seletor do modal pela área de upload.")
        async with self.page.expect_file_chooser(timeout=self.settings.timeout_ms) as chooser_event:
            await self._click_once(dropzone)
        chooser = await chooser_event.value
        await chooser.set_files([str(image) for image in batch])
        self.logger.info("Seletor dinâmico recebeu %s arquivo(s).", len(batch))
        return len(batch)

    async def _wait_for_upload_result(self) -> None:
        """Aguarda a confirmação do servidor antes de procurar os cards.

        Nenhum refresh ocorre nesta etapa por falta de um input local: somente
        um toast explícito de erro, rate limit ou o prazo de servidor vencido
        pode reiniciar o grupo.
        """
        deadline = time.monotonic() + self.settings.upload_result_timeout
        self.logger.info(
            "Carregar clicado; aguardando exclusivamente o toast verde de confirmação do upload no Vibes. "
            "O texto do botão não é confirmação."
        )
        while time.monotonic() < deadline:
            await self._raise_if_platform_error()
            if await is_visible(self.page, RATE_LIMIT_ALERT):
                self.audit.record("upload_result_detected", result="rate_limit")
                raise RateLimitDetected("Rate limit durante o upload")
            if await is_visible(self.page, UPLOAD_ERROR_ALERT) or await is_visible(self.page, ERROR_ALERT):
                self.audit.record("upload_result_detected", result="error")
                raise RuntimeError("O Vibes informou erro após clicar em Carregar.")
            # Erro vem antes de sucesso: assim um toast verde residual de um
            # grupo anterior jamais mascara o erro do envio que acabou de ocorrer.
            if await is_visible(self.page, UPLOAD_SUCCESS_ALERT):
                toast = await first_visible(self.page, UPLOAD_SUCCESS_ALERT, 1_000)
                message = (await toast.inner_text()).strip().replace("\n", " ")
                self.logger.info("Toast verde de upload confirmado: %s", message)
                self.audit.record("upload_result_detected", result="success", message=message)
                return
            await asyncio.sleep(0.5)
        raise PlaywrightTimeoutError(
            f"O Vibes não confirmou o upload por toast em {self.settings.upload_result_timeout}s."
        )

    async def _media_count(self) -> int:
        """Conta os cards da criação pela âncora estável da UI do Vibes."""
        return await MEDIA_THUMBNAIL[0](self.page).count()

    async def _bind_media_ids(self, images: list[Path]) -> None:
        """Salva o ID do Vibes de cada card antes que animações mudem a ordem.

        O Vibes não expõe o nome local nos thumbnails. O atributo
        ``data-analytics-media-id`` sobrevive a reordenações da galeria e
        permite selecionar a mesma imagem depois que uma animação concluída
        for movida para o topo.
        """
        if not images or all(self.checkpoint.media_id_for(image) for image in images):
            return
        await self._wait_for_media_count(len(images))
        cards = MEDIA_THUMBNAIL[0](self.page)
        media_ids = await cards.evaluate_all("elements => elements.map(element => element.dataset.analyticsMediaId || '')")
        if len(media_ids) < len(images) or any(not media_id for media_id in media_ids[:len(images)]):
            raise PlaywrightTimeoutError("A galeria não forneceu IDs estáveis para todas as mídias enviadas.")
        for image, media_id in zip(images, media_ids, strict=True):
            self.checkpoint.bind_media_id(image, media_id)
        self.logger.info("IDs estáveis associados a %s mídia(s) da galeria.", len(images))
        self.audit.record("media_ids_bound", count=len(images))

    async def _wait_for_media_count(self, expected_count: int) -> int:
        """Só libera o próximo lote quando o lote confirmado for renderizado.

        A página pode completar o upload antes de desenhar os thumbnails. Como
        já existe toast verde, aqui apenas esperamos/recarregamos a galeria;
        nunca voltamos a selecionar nem reenviar os mesmos arquivos.
        """
        async def operation() -> int:
            deadline = time.monotonic() + self.settings.timeout_ms / 1000
            last_count = 0
            while time.monotonic() < deadline:
                await self._raise_if_platform_error()
                last_count = await self._media_count()
                if last_count >= expected_count:
                    self.logger.info("Galeria confirmada | cards=%s | esperado=%s", last_count, expected_count)
                    return last_count
                await asyncio.sleep(0.5)
            raise PlaywrightTimeoutError(
                f"A galeria mostrou {last_count} card(s), mas eram esperados pelo menos {expected_count}."
            )

        async def recovery(exc: Exception) -> None:
            self.logger.warning(
                "Toast já confirmou o envio; aguardando renderização da galeria, sem reenviar arquivos: %s", exc
            )
            await self._recover(exc)

        return await self.retry.retry_until_success(
            f"renderização de {expected_count} cards na galeria",
            operation,
            recovery,
            retry_delay_seconds=self.settings.error_retry_delay,
        )

    async def _click_once(self, locator) -> None:
        """Clique com o mesmo feedback visual, usado pela transação de upload."""
        if self.settings.show_click_highlight:
            await show_click_target(locator, self.settings.click_highlight_duration_ms)
        await locator.click()

    async def _find_image(self, image: Path, *, recover: bool = True):
        return await self.retry.retry_locator(
            f"card da imagem {image.name}", lambda: self._find_image_once(image), self._recover if recover else None,
        )

    async def _find_image_once(self, image: Path):
        if self.settings.image_card_selector:
            locator = self.page.locator(self.settings.image_card_selector).filter(has_text=image.name).first
            await locator.wait_for(state="visible", timeout=self.settings.timeout_ms)
            return locator
        # Dentro do editor, a coluna esquerda preserva o nome do arquivo no
        # alt do thumbnail e cada item é um botão. É a rota prioritária, pois
        # continua válida mesmo depois que vídeos concluídos sobem para o topo.
        side_thumbnail = self.page.get_by_role("button").filter(
            has=self.page.get_by_alt_text(image.name, exact=True)
        )
        if await side_thumbnail.first.is_visible():
            return side_thumbnail.first
        # No editor a coluna é virtualizada: um thumbnail fora da viewport não
        # existe no DOM. Não caímos no card do grid (que não está nesta tela),
        # pois isso criaria uma espera longa e escolheria uma mídia errada.
        if await is_visible(self.page, EDIT_IMAGE_TITLE) or await is_visible(self.page, EDIT_VIDEO_TITLE):
            raise PlaywrightTimeoutError(
                f"O thumbnail de {image.name} não está visível na coluna esquerda do editor."
            )
        media_id = self.checkpoint.media_id_for(image)
        if media_id:
            locator = self.page.locator(
                f'[role="button"][data-analytics-id="creation_gallery.thumbnail_click"][data-analytics-media-id="{media_id}"]'
            )
            await locator.wait_for(state="visible", timeout=self.settings.timeout_ms)
            return locator
        # Fallback apenas para a primeira vinculação. Após a primeira animação
        # os cards podem mudar de posição, por isso os IDs acima são a rota
        # normal e persistida de seleção.
        try:
            position = self._media_order.index(image)
        except ValueError as exc:
            raise PlaywrightTimeoutError(f"Não há posição conhecida para a mídia {image.name}.") from exc
        locator = MEDIA_THUMBNAIL[0](self.page).nth(position)
        await locator.wait_for(state="visible", timeout=self.settings.timeout_ms)
        return locator

    async def _editor_kind(self) -> str:
        """Aguarda a abertura do editor e diferencia imagem original de vídeo."""
        deadline = time.monotonic() + self.settings.timeout_ms / 1000
        while time.monotonic() < deadline:
            await self._raise_if_platform_error()
            if await is_visible(self.page, EDIT_VIDEO_TITLE):
                return "video"
            if await is_visible(self.page, EDIT_IMAGE_TITLE):
                return "image"
            await asyncio.sleep(0.5)
        raise PlaywrightTimeoutError("O Vibes não abriu o editor de imagem nem o editor de vídeo.")

    async def _selected_editor_for(self, image: Path) -> str:
        """Confirma que o thumbnail clicado abriu o editor da mídia esperada."""
        kind = await self._editor_kind()
        if kind == "video":
            return kind
        await self._wait_for_visible(
            (lambda page: page.get_by_text(image.name, exact=False),),
            self.settings.timeout_ms,
        )
        return kind

    async def _scroll_editor_sidebar_to_bottom(self) -> list[str]:
        """Alcança o fim físico da coluna virtualizada de mídias do Vibes.

        O Vibes mantém no DOM apenas os thumbnails renderizados. Logo, o
        último ``button`` contado pode ser apenas o último *visível*, não o
        último da lista. A rolagem é aplicada ao ancestral com overflow até a
        extremidade e só é aceita após duas leituras estáveis do final.
        """
        scroll_script = """
        (element) => {
          let node = element.parentElement;
          while (node) {
            const overflow = getComputedStyle(node).overflowY;
            if (/(auto|scroll)/.test(overflow)) {
              const scrollable = node.scrollHeight > node.clientHeight;
              if (scrollable) {
                node.scrollTop = node.scrollHeight;
              }
              return {
                found: true,
                scrollable,
                top: node.scrollTop,
                height: node.scrollHeight,
                client: node.clientHeight,
                // Quando todos os thumbnails cabem na tela, não existe
                // scroll físico a executar: a lista já está integralmente no
                // seu fim e pode ser usada como uma coluna normal.
                atBottom: !scrollable || node.scrollTop + node.clientHeight >= node.scrollHeight - 2,
              };
            }
            node = node.parentElement;
          }
          return { found: false };
        }
        """
        previous_last = ""
        stable_reads = 0
        for attempt in range(1, 13):
            button = await first_visible(self.page, EDITOR_SIDEBAR_THUMBNAILS, self.settings.timeout_ms)
            metrics = await button.evaluate(scroll_script)
            if not metrics.get("found"):
                raise PlaywrightTimeoutError("Não foi encontrado o contêiner rolável da coluna esquerda do editor.")

            # Esta espera curta não é uma pausa arbitrária: dá um ciclo para a
            # lista virtualizada materializar os itens depois do scroll.
            await self.page.wait_for_timeout(250)
            buttons = EDITOR_SIDEBAR_THUMBNAILS[0](self.page)
            media_names = await buttons.evaluate_all(
                "elements => elements.map(element => element.querySelector('img[alt]')?.getAttribute('alt') || '')"
            )
            last_name = media_names[-1] if media_names else ""
            if metrics.get("atBottom") and last_name and (
                not metrics.get("scrollable") or last_name == previous_last
            ):
                stable_reads += 1
            else:
                stable_reads = 0
            previous_last = last_name
            if stable_reads >= 1:
                self.logger.info(
                    "Coluna esquerda pronta | tentativa=%s | rolável=%s | scroll=%s/%s | último=%s",
                    attempt, metrics.get("scrollable"), metrics.get("top"), metrics.get("height"), last_name,
                )
                self.audit.record(
                    "editor_sidebar_bottom_reached", attempt=attempt, scroll_top=metrics.get("top"),
                    scroll_height=metrics.get("height"), last_thumbnail=last_name,
                )
                return media_names
        raise PlaywrightTimeoutError("A coluna esquerda não estabilizou no fim físico da lista de mídias.")

    async def _next_sidebar_image_from_bottom(self, pending_by_name: dict[str, Path]) -> Path:
        """Rola até o fim real e devolve a última mídia pendente."""
        media_names = await self._scroll_editor_sidebar_to_bottom()
        skipped_after_candidate: list[str] = []
        for name in reversed(media_names):
            image = pending_by_name.get(name)
            if image and not self.checkpoint.is_complete(image):
                self.logger.info(
                    "Próxima mídia pela coluna esquerda (de baixo para cima): %s | "
                    "itens finais já concluídos/fora da seleção=%s",
                    image.name, ", ".join(skipped_after_candidate) or "nenhum",
                )
                return image
            skipped_after_candidate.append(name)
        raise PlaywrightTimeoutError("Nenhuma mídia pendente da seleção atual foi encontrada na coluna esquerda.")

    async def _animate_sidebar_bottom_up(self, images: list[Path]) -> list[Path]:
        """Processa de baixo para cima e repete apenas a fila de falhas.

        Uma mídia que excede as tentativas da rodada é adiada, não perdida.
        Depois de percorrer todas as demais, uma nova rodada contém somente
        essas pendências. O laço termina apenas quando todas forem sucesso ou
        vídeo já existente.
        """
        round_images = [image for image in images if not self.checkpoint.is_complete(image)]
        round_number = 0
        while round_images:
            round_number += 1
            deferred: list[Path] = []
            # A reserva começa uma rodada nova com cinco chances novas. Já
            # uma execução interrompida no meio da rodada normal preserva o
            # contador no checkpoint e retoma exatamente de onde parou.
            for image in round_images:
                if round_number > 1 or self.checkpoint.status_for(image) == "deferred":
                    self.checkpoint.update(
                        image, "retrying", attempts=0, generation_error_count=0,
                        reason="deferred_round_started", deferred_round=max(0, round_number - 1),
                    )
            pending_by_name = {image.name: image for image in round_images}
            self.logger.info(
                "Rodada de animação %s iniciada | mídias=%s", round_number, len(pending_by_name)
            )
            self.audit.record(
                "animation_round_started", round=round_number,
                images=[image.name for image in round_images],
            )
            while pending_by_name:
                image = await self.retry.retry_until_success(
                    "localizar última mídia pendente na coluna esquerda",
                    lambda: self._next_sidebar_image_from_bottom(pending_by_name),
                    self._recover,
                    retry_delay_seconds=self.settings.error_retry_delay,
                )
                completed_or_skipped = await self._animate_image(image)
                pending_by_name.pop(image.name, None)
                if not completed_or_skipped:
                    deferred.append(image)

            if not deferred:
                self.logger.info("Rodada %s concluída sem pendências.", round_number)
                self.audit.record("animation_round_completed", round=round_number, deferred_count=0)
                return []

            self.logger.warning(
                "Rodada %s terminou com %s mídia(s) adiada(s): %s",
                round_number, len(deferred), ", ".join(image.name for image in deferred),
            )
            self.audit.record(
                "animation_round_completed", round=round_number, deferred_count=len(deferred),
                deferred_images=[image.name for image in deferred],
            )
            # A rodada 1 é o percurso normal. Só as rodadas 2, 3 e 4 são
            # ciclos exclusivos da reserva. Depois de três ciclos completos
            # ainda falhando, interrompemos para revisão humana.
            deferred_rounds_completed = round_number - 1
            if deferred_rounds_completed >= self.settings.max_deferred_rounds:
                for image in deferred:
                    self.checkpoint.update(
                        image, "failed_final", attempts=self.settings.max_generation_errors_per_round,
                        reason="max_deferred_rounds_reached", deferred_rounds=deferred_rounds_completed,
                    )
                self.logger.error(
                    "Limite de %s rodadas de retentativa atingido. Automação encerrada para revisão manual: %s",
                    self.settings.max_deferred_rounds, ", ".join(image.name for image in deferred),
                )
                self.audit.record(
                    "manual_review_required", deferred_rounds=deferred_rounds_completed,
                    images=[image.name for image in deferred],
                )
                return deferred
            if self.settings.deferred_round_wait:
                self.logger.info(
                    "Aguardando %ss antes da rodada %s, apenas com as mídias adiadas.",
                    self.settings.deferred_round_wait, round_number + 1,
                )
                await asyncio.sleep(self.settings.deferred_round_wait)
            round_images = deferred

        return []

    def _final_evaluation(self, files: list[Path], duplicates: list[Path], final_failures: list[Path]) -> None:
        """Registra o balanço terminal sem confundir vídeo, duplicata e erro."""
        successful = [image for image in files if self.checkpoint.status_for(image) == "success"]
        existing_videos = [image for image in files if self.checkpoint.status_for(image) == "skipped_video"]
        failed = [image for image in files if self.checkpoint.status_for(image) == "failed_final"]
        unresolved = [
            image for image in files
            if self.checkpoint.status_for(image) not in {"success", "skipped_video", "failed_final"}
        ]
        report = {
            "input_images": len(files) + len(duplicates),
            "unique_images": len(files),
            "duplicates_ignored": len(duplicates),
            "success": len(successful),
            "existing_videos": len(existing_videos),
            "failed_final": len(failed),
            "unresolved": len(unresolved),
            "duplicate_images": [image.name for image in duplicates],
            "failed_images": [image.name for image in failed],
            "unresolved_images": [image.name for image in unresolved],
        }
        self.audit.record("final_evaluation", **report)
        if final_failures or failed or unresolved:
            self.logger.error(
                "Avaliação final | sucesso=%s | vídeos existentes=%s | duplicatas ignoradas=%s | "
                "erros finais=%s | pendências inesperadas=%s. Processo encerrado para revisão manual.",
                len(successful), len(existing_videos), len(duplicates), len(failed), len(unresolved),
            )
            return
        self.logger.info(
            "Avaliação final aprovada | sucesso=%s | vídeos existentes=%s | duplicatas ignoradas=%s.",
            len(successful), len(existing_videos), len(duplicates),
        )
        self.audit.record("run_completed", image_count=len(files), **report)

    async def _stabilize_after_animation_success(self, image: Path) -> None:
        """Espera o toast sair e recarrega antes de escolher o próximo item.

        Ao concluir, o Vibes move o vídeo criado para o topo. Escolher o
        próximo thumbnail antes de a lista terminar essa reorganização pode
        selecionar um vídeo. O refresh preserva a URL da mídia e reconstrói a
        coluna em um estado consistente.
        """
        async def operation() -> None:
            deadline = time.monotonic() + min(self.settings.timeout_ms / 1000, 15)
            while time.monotonic() < deadline:
                await self._raise_if_platform_error()
                if not await is_visible(self.page, SUCCESS_ALERT):
                    self.logger.info("Toast de sucesso encerrado; atualizando editor antes da próxima mídia.")
                    self.audit.record("animation_success_toast_dismissed", image=image.name)
                    await self.retry.retry_refresh(self.page)
                    await self._editor_kind()
                    await first_visible(self.page, EDITOR_SIDEBAR_THUMBNAILS, self.settings.timeout_ms)
                    self.audit.record("editor_refreshed_after_animation", image=image.name, url=self.page.url)
                    return
                await self.page.wait_for_timeout(250)
            # Um toast preso não impede o próximo trabalho: o refresh é a
            # própria recuperação segura para remover a UI transitória.
            self.logger.warning("Toast de sucesso permaneceu visível por 15s; atualizando o editor mesmo assim.")
            self.audit.record("animation_success_toast_stuck", image=image.name)
            await self.retry.retry_refresh(self.page)
            await self._editor_kind()

        await self.retry.retry_until_success(
            "estabilizar editor após animação concluída",
            operation,
            self._recover,
            retry_delay_seconds=self.settings.error_retry_delay,
        )

    async def _animate_image(self, image: Path) -> bool:
        """Anima uma mídia; retorna ``False`` quando ela deve ser adiada."""
        previous = self.checkpoint.details_for(image)
        attempts = int(previous.get("attempts", previous.get("attempt", 0)) or 0)
        # ``consecutive_errors`` é o nome gravado por versões anteriores;
        # mantemos a migração para que uma mídia já em falha não volte a zero.
        consecutive_generation_errors = int(
            previous.get("generation_error_count", previous.get("consecutive_errors", 0)) or 0
        )
        while not self.checkpoint.is_complete(image):
            attempts += 1
            self.checkpoint.update(image, self.state.value, attempt=attempts)
            try:
                self._set_state(State.SELECT_IMAGE, image)
                await self._bounded_image_step(
                    f"selecionar {image.name}",
                    self.retry.retry_click(
                        f"selecionar {image.name}", lambda: self._find_image(image, recover=False),
                        verify=lambda: self._selected_editor_for(image), recovery=None,
                    ),
                )
                if await self._selected_editor_for(image) == "video":
                    self.checkpoint.update(image, "skipped_video", attempts=attempts)
                    self.logger.info("Vídeo já existente ignorado: %s", image.name)
                    self.audit.record("video_skipped", image=image.name, attempts=attempts)
                    return True
                self._set_state(State.MANUAL_ANIMATE, image)
                await self._bounded_image_step(
                    "Manual animate",
                    self.retry.retry_click(
                        "Manual animate", lambda: self._locator("Manual animate", MANUAL_ANIMATE_BUTTON, recover=False),
                        verify=lambda: self._wait_for_visible(PROMPT_TEXTAREA, self.settings.timeout_ms), recovery=None,
                    ),
                )
                self._set_state(State.INSERT_PROMPT, image)
                await self._bounded_image_step(
                    "preencher prompt",
                    self.retry.retry_fill(
                        "preencher prompt", lambda: self._locator("textarea do prompt", PROMPT_TEXTAREA, recover=False),
                        self.prompt, None,
                    ),
                )
                self._set_state(State.CLICK_ANIMATE, image)
                await self._bounded_image_step(
                    "Animate",
                    self.retry.retry_click("Animate", lambda: self._locator("Animate", ANIMATE_BUTTON, recover=False), recovery=None),
                )
                self._set_state(State.WAIT_RESULT, image)
                outcome = await self._wait_for_result()
                if outcome == "success":
                    self._set_state(State.SUCCESS, image)
                    self.checkpoint.update(image, "success", attempts=attempts)
                    self.logger.info("Animação concluída: %s", image.name)
                    self.audit.record("image_completed", image=image.name, attempts=attempts)
                    await self._stabilize_after_animation_success(image)
                    return True
                if outcome == "rate_limit":
                    raise RateLimitDetected()
                self._set_state(State.ERROR, image)
                consecutive_generation_errors += 1
                self.checkpoint.update(image, "retrying", attempts=attempts, reason="error_alert",
                                       generation_error_count=consecutive_generation_errors, consecutive_errors=consecutive_generation_errors)
                await self._dismiss_and_refresh()
                if not await self._retry_or_defer_image(image, attempts, consecutive_generation_errors, "generation_error"):
                    return False
            except RateLimitDetected:
                self._set_state(State.RATE_LIMIT, image)
                self.checkpoint.update(image, "rate_limit", attempts=attempts)
                self.logger.warning("Rate limit detectado. Aguardando %ss.", self.settings.rate_limit_wait)
                self.audit.record("rate_limit_detected", image=image.name, attempts=attempts, wait_seconds=self.settings.rate_limit_wait)
                await asyncio.sleep(self.settings.rate_limit_wait)
                await self._recover(RateLimitDetected("fim da espera de rate limit"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("Falha recuperável em %s: %s", image.name, exc)
                consecutive_generation_errors += 1
                self._set_state(State.ERROR, image)
                self.checkpoint.update(
                    image, "retrying", attempts=attempts, reason=f"technical_failure:{type(exc).__name__}",
                    generation_error_count=consecutive_generation_errors, consecutive_errors=consecutive_generation_errors,
                )
                self.audit.record("image_recovery", image=image.name, attempts=attempts, error_type=type(exc).__name__, error=str(exc))
                try:
                    await self._recover(exc)
                except BrowserSessionClosed:
                    self.logger.error("Firefox fechado; automação encerrada sem ficar em retry infinito.")
                    raise
                if not await self._retry_or_defer_image(image, attempts, consecutive_generation_errors, "technical_failure"):
                    return False

    async def _bounded_image_step(self, name: str, operation) -> None:
        """Evita que um botão instável consuma a foto inteira em retry infinito."""
        try:
            await asyncio.wait_for(operation, timeout=self.settings.image_step_timeout)
        except TimeoutError as exc:
            raise PlaywrightTimeoutError(
                f"{name} não se estabilizou em {self.settings.image_step_timeout}s."
            ) from exc

    async def _retry_or_defer_image(self, image: Path, attempts: int, errors: int, reason: str) -> bool:
        """Aplica o orçamento de cinco falhas a qualquer erro da mesma mídia."""
        if errors >= self.settings.max_generation_errors_per_round:
            self._set_state(State.DEFERRED, image)
            self.checkpoint.update(
                image, "deferred", attempts=attempts, reason=f"{reason}_exhausted",
                generation_error_count=errors, consecutive_errors=errors,
            )
            self.logger.warning("Mídia adiada após %s/%s falhas: %s", errors, self.settings.max_generation_errors_per_round, image.name)
            self.audit.record("image_deferred_after_errors", image=image.name, attempts=attempts, errors=errors, reason=reason)
            return False
        wait_seconds = self.settings.repeated_error_wait if errors >= self.settings.repeated_error_threshold else self.settings.error_retry_delay
        self.logger.warning(
            "Falha %s/%s (%s) em %s; aguardando %ss antes de repetir a mesma imagem.",
            errors, self.settings.max_generation_errors_per_round, reason, image.name, wait_seconds,
        )
        await asyncio.sleep(wait_seconds)
        return True

    async def _wait_for_result(self) -> str:
        deadline = time.monotonic() + self.settings.result_timeout
        while time.monotonic() < deadline:
            await self._raise_if_platform_error()
            if await is_visible(self.page, SUCCESS_ALERT):
                self.audit.record("result_detected", result="success")
                return "success"
            if await is_visible(self.page, RATE_LIMIT_ALERT):
                self.audit.record("result_detected", result="rate_limit")
                return "rate_limit"
            if await is_visible(self.page, ERROR_ALERT):
                self.audit.record("result_detected", result="error")
                return "error"
            # Polling curto é intencional: mensagens de toast podem desaparecer
            # rápido e a plataforma não disponibiliza um evento confiável.
            await asyncio.sleep(1)
        raise PlaywrightTimeoutError("Nenhum resultado foi detectado no prazo configurado.")

    async def _dismiss_and_refresh(self) -> None:
        if await is_visible(self.page, CLOSE_POPUP_BUTTON):
            try:
                await (await first_visible(self.page, CLOSE_POPUP_BUTTON, 1_000)).click()
            except Exception as exc:
                self.logger.info("Popup não pôde ser fechado diretamente: %s", exc)
        await self._recover(RuntimeError("erro informado pela plataforma"))
