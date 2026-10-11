import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/**
 * The dev server proxies `/api` and `/ws` to the Python server so a browser on the
 * laptop can use the app with no CORS setup and no absolute address.
 *
 * The API port is **8888**, not 8000: port 8000 was already taken on the machine this
 * was built on, and it is the busiest default in existence. It is read from `.env` so
 * that changing `PORT` on the server is the only edit needed, and overridable per
 * command with `VITE_API_PORT`:
 *
 *     $env:VITE_API_PORT = "8000"; npm run dev
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiPort = env.VITE_API_PORT || env.PORT || "8888";
  const host = env.VITE_API_HOST || "127.0.0.1";
  const target = `http://${host}:${apiPort}`;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      // Listen on every interface, or a phone on the same WiFi cannot open the dev
      // server. `--host 0.0.0.0` on the command line does the same thing.
      host: true,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
        },
        "/ws": {
          target: target.replace(/^http/, "ws"),
          ws: true,
        },
      },
    },
    preview: {
      port: 4173,
      host: true,
    },
  };
});
