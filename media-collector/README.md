# Media Collector — teste local para Firefox

Protótipo local para detectar **apenas vídeos** diretos e não protegidos que o Firefox requisitou ou que já estão carregados na aba ativa. Não remove marca d'água, não acessa DRM, não descriptografa conteúdo e não reconstrói streams HLS/DASH (`.m3u8`/`.mpd`).

## Teste no Firefox

1. Abra `about:debugging#/runtime/this-firefox`.
2. Clique em **Carregar extensão temporária**.
3. Selecione [manifest.json](manifest.json).
4. Abra uma página cujo vídeo você tem permissão para baixar; reproduza e, se ela carregar itens ao rolar, percorra a página.
5. Clique no ícone **Media Collector**. O painel também faz uma varredura de `<video>`, `source`, atributos de mídia e recursos já carregados.
6. Use **Varrer página** depois de rolar para carregar mais itens. O contador mostra vídeos diretos e streams adaptativos separadamente.
7. **Baixar todos** envia à fila normal do Firefox a maior variante direta reconhecida de cada vídeo, em `Downloads/media-collector/`. O botão individual mantém disponível qualquer variante específica listada.

A resolução vem da dimensão exposta pelo elemento de vídeo quando disponível; caso contrário, é estimada pelo URL. A escolha de “melhor variante” só agrupa URLs com o mesmo caminho e sufixos claros de qualidade (`720p`, `1080p`, `4k`), para não descartar vídeos distintos por suposição.

Use **Limpar** para reiniciar a lista da aba. Como a extensão é temporária, ela sai quando o Firefox é fechado.

## Instalação permanente no Firefox comum

O Firefox Release exige uma assinatura digital da Mozilla para extensões permanentes. Este projeto já possui um ID fixo e pode ser enviado como extensão privada, sem publicação na loja:

1. Execute `./package.ps1` no PowerShell dentro desta pasta. Ele gera `dist/media-collector-0.2.0-unsigned.xpi`.
2. Entre no [AMO Developer Hub](https://addons.mozilla.org/developers/), escolha enviar uma nova extensão e marque **On your own** / auto-distribuição (não listada).
3. Envie o arquivo `.xpi`, aguarde a assinatura e baixe o `.xpi` assinado devolvido pela Mozilla.
4. Arraste o arquivo assinado para `about:addons` ou abra-o no Firefox. A extensão então sobrevive a reinicializações e atualizações do navegador.

O arquivo fica privado e pode ser distribuído somente por você. Para uma versão nova, altere a versão do manifesto, gere outro pacote e envie como atualização no mesmo painel AMO. A assinatura é obrigatória no Firefox Release; `about:debugging` continua sendo apenas o modo temporário.

O manifesto declara `data_collection_permissions.required = ["none"]`: a extensão não envia dados para serviços externos nem mantém histórico fora do navegador. Ela apenas observa a aba ativa e pede ao próprio Firefox para baixar URLs diretas escolhidas pelo usuário.

## Limites intencionais

- apenas arquivos diretos de vídeo são elegíveis ao download;
- manifests HLS/DASH são informados, mas não baixados/montados;
- conteúdo com DRM não é suportado;
- vídeos carregados por URLs `blob:` não têm arquivo direto identificável;
- a extensão só examina a aba ativa e não envia histórico para servidor.
