import path from "node:path";
import { fileURLToPath } from "node:url";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const webNodeModules = path.resolve(dirname, "../../apps/web/node_modules");

export default {
  resolve: {
    alias: {
      react: path.join(webNodeModules, "react"),
      "react/jsx-runtime": path.join(webNodeModules, "react/jsx-runtime.js"),
      "react-dom/client": path.join(webNodeModules, "react-dom/client.js"),
      "@rive-app/react-webgl2": path.join(
        webNodeModules,
        "@rive-app/react-webgl2/dist/index.js",
      ),
      "@rive-app/webgl2": path.join(
        webNodeModules,
        "@rive-app/webgl2/rive.js",
      ),
      "@testing-library/react": path.join(
        webNodeModules,
        "@testing-library/react/dist/index.js",
      ),
    },
  },
  test: {
    environment: "jsdom",
  },
};
