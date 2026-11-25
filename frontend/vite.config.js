import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 5173 is in the recommendation API's default CORS allow-list (REC_API_CORS_ORIGINS).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
