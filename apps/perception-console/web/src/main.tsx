import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import TabletApp from "./TabletApp";
import { isAndroidApp } from "./tablet-api";
import "./styles.css";
import "./console-theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{isAndroidApp() || window.location.pathname === "/tablet" ? <TabletApp /> : <App />}</React.StrictMode>,
);
