import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Admin panel: local only, no login by design. Bind to 127.0.0.1 (never 0.0.0.0 / --host).
// Product images are the customer site's local copies (frontend/public/products), served
// read-only from that folder so they aren't duplicated.
export default defineConfig({
  plugins: [react()],
  publicDir: "../frontend/public",
  server: { host: "127.0.0.1", port: 5174, strictPort: true },
  preview: { host: "127.0.0.1", port: 4174, strictPort: true },
});
