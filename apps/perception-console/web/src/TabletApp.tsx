import { useEffect, useState, type FormEvent } from "react";
import Home3D from "./Home3D";
import type { HomeSnapshot } from "./home-view-data";
import { isAndroidApp, loadTabletSnapshot } from "./tablet-api";
import { normalizeTabletOrigin } from "./tablet-connection";

const STORAGE_KEY = "smart-home.tablet-origin";
const nativeApp = isAndroidApp();

function savedOrigin(): string | null {
  if (!nativeApp) return null;
  try { return normalizeTabletOrigin(localStorage.getItem(STORAGE_KEY) ?? ""); }
  catch { return null; }
}

export default function TabletApp() {
  const [home, setHome] = useState<HomeSnapshot>({});
  const [stale, setStale] = useState(true);
  const [origin, setOrigin] = useState<string | null>(savedOrigin);
  const [draft, setDraft] = useState(origin ?? "");
  const [editing, setEditing] = useState(nativeApp && !origin);
  const [connectionError, setConnectionError] = useState("");
  const [controlBusy, setControlBusy] = useState(false);

  useEffect(() => {
    if (nativeApp && !origin) return;
    let active = true;
    let timeout: number | undefined;
    const poll = async () => {
      try {
        const snapshot = await loadTabletSnapshot(origin);
        if (active) { setHome(snapshot); setStale(false); setConnectionError(""); }
      } catch {
        if (active) { setStale(true); setConnectionError("暂时无法取得家庭数据，请检查电脑服务和网络连接。"); }
      } finally {
        if (active) timeout = window.setTimeout(() => void poll(), 1500);
      }
    };
    void poll();
    return () => { active = false; if (timeout !== undefined) window.clearTimeout(timeout); };
  }, [origin]);

  function saveConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = normalizeTabletOrigin(draft);
    if (!next) {
      setConnectionError("请输入 HTTPS 站点根地址，不要附加 /tablet、账号或参数。");
      return;
    }
    try { localStorage.setItem(STORAGE_KEY, next); }
    catch { setConnectionError("无法保存连接地址，请检查平板存储设置。"); return; }
    setOrigin(next);
    setDraft(next);
    setHome({});
    setStale(true);
    setConnectionError("");
    setEditing(false);
  }

  return <div className="shell home-mode tablet-shell">
    {nativeApp && <div className="tablet-native-bar"><span>小屋 · 我的家</span><button type="button" disabled={controlBusy} onClick={() => { setDraft(origin ?? ""); setEditing(true); }}>连接设置</button></div>}
    {nativeApp && editing ? <main className="tablet-setup">
      <div className="tablet-setup-card">
        <p className="tablet-setup-kicker">连接家里的电脑</p>
        <h1>设置家庭服务地址</h1>
        <p>先在平板连接 Tailscale，再输入电脑提供的 HTTPS 根地址。连接后可查看家庭状态并调整设备设置。</p>
        <form onSubmit={saveConnection}>
          <label htmlFor="tablet-server">HTTPS 地址</label>
          <input id="tablet-server" type="url" inputMode="url" autoCapitalize="off" autoComplete="url" spellCheck={false} placeholder="https://设备名.网络名.ts.net" value={draft} onChange={(event) => setDraft(event.target.value)} />
          {connectionError && <p className="tablet-connection-error" role="alert">{connectionError}</p>}
          <button type="submit">连接并查看</button>
          {origin && <button type="button" className="tablet-secondary-button" onClick={() => { setConnectionError(""); setEditing(false); }}>返回我的家</button>}
        </form>
      </div>
    </main> : <main className="home-main">
      {nativeApp && connectionError && <p className="tablet-connection-error tablet-status" role="status">{connectionError}</p>}
      <Home3D home={home} stale={stale} tabletMode controlOrigin={origin} onControlBusyChange={setControlBusy} />
    </main>}
  </div>;
}
