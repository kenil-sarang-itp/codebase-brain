/**
 * Application entry point.
 *
 * Mounts the React tree into #root and pulls in the global stylesheet. Kept
 * deliberately tiny — all real composition happens in <App/>.
 */
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
