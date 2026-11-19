import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { FailoverStory } from "./demo/FailoverStory";
import "./styles.css";
import "./demo/demo.css";

function Root() {
  const [tab, setTab] = useState<"story" | "loadtest">("story");
  return (
    <>
      <div className="tabs">
        <button className={tab === "story" ? "active" : ""} onClick={() => setTab("story")}>Failover storyline</button>
        <button className={tab === "loadtest" ? "active" : ""} onClick={() => setTab("loadtest")}>Load test</button>
      </div>
      {tab === "story" ? <FailoverStory /> : <App />}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
