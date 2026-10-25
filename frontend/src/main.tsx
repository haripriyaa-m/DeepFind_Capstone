import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Fonts are bundled (the app is offline).
import "@fontsource-variable/archivo/wdth.css";      // display: condensed heavy grotesk
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-serif/400-italic.css";
import "@fontsource/ibm-plex-serif/500.css";

import "./styles/tokens.css";
import "./styles/app.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
