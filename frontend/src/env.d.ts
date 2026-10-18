/** Frontend version from package.json, injected by vite.config.ts. */
declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  /** build identifier baked into the image (frontend Dockerfile ARG) */
  readonly VITE_BUILD_VERSION?: string;
}
