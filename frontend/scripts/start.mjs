import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectDir = resolve(frontendDir, "..");
const viteBin = resolve(frontendDir, "node_modules", "vite", "bin", "vite.js");
let stopping = false;
let backend;
let vite;

async function assertPortAvailable(port, service) {
  await new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", error => reject(error));
    probe.listen(port, "127.0.0.1", () => probe.close(resolve));
  }).catch(() => {
    throw new Error(
      `${service} já está usando a porta ${port}. Pare a instância anterior com Ctrl+C antes de executar npm start novamente.`,
    );
  });
}

try {
  await assertPortAvailable(8000, "O backend");
  await assertPortAvailable(5173, "O painel");
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

function terminateTree(child) {
  if (!child?.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    // O Python Launcher e o Vite podem criar filhos; /T derruba todo o grupo.
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
    return;
  }
  child.kill("SIGTERM");
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  terminateTree(backend);
  terminateTree(vite);
  process.exit(code);
}

async function waitForBackend() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch("http://127.0.0.1:8000/api/catalog");
      if (response.ok) return;
    } catch { /* o Uvicorn ainda está inicializando */ }
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error("O backend não respondeu em 30 segundos.");
}

backend = spawn(
  "python",
  ["-m", "uvicorn", "backend.src.main:app", "--host", "127.0.0.1", "--port", "8000"],
  { cwd: projectDir, stdio: "inherit" },
);

backend.on("error", error => {
  console.error(`Não foi possível iniciar o backend: ${error.message}`);
  stop(1);
});
backend.on("exit", code => {
  if (!stopping) {
    console.error(`Backend encerrado${code === 0 ? "" : ` com código ${code}`}.`);
    stop(code || 1);
  }
});

try {
  await waitForBackend();
  vite = spawn(process.execPath, [viteBin], { cwd: frontendDir, stdio: "inherit" });
  vite.on("error", error => {
    console.error(`Não foi possível iniciar o painel: ${error.message}`);
    stop(1);
  });
  vite.on("exit", code => {
    if (!stopping) {
      console.error(`Painel encerrado${code === 0 ? "" : ` com código ${code}`}.`);
      stop(code || 1);
    }
  });
} catch (error) {
  console.error(error.message);
  stop(1);
}

process.once("SIGINT", () => stop(0));
process.once("SIGTERM", () => stop(0));
