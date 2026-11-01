import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import TabletApp from "./TabletApp";
import ModelWalkthrough from './ModelWalkthrough';
import { isAndroidApp } from "./tablet-api";
import "./styles.css";
import "./console-theme.css";

// 网页直接访问 /walkthrough；Android 的本地页面用 ?viewer=walkthrough 打开同一漫游组件。
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{window.location.pathname==='/walkthrough'||new URLSearchParams(window.location.search).get('viewer')==='walkthrough' ? <ModelWalkthrough /> : isAndroidApp() || window.location.pathname === "/tablet" ? <TabletApp /> : <App />}</React.StrictMode>,
);
