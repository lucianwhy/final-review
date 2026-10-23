import { useEffect, useState } from "react";
import { api } from "./workbench-api";

type Account = { email: string | null; local: boolean };

export default function AccountStatus() {
  const [account, setAccount] = useState<Account | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api<Account>("/api/auth/me").then(value => {
      if (active) setAccount(value);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  async function switchAccount() {
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/sign-out", "POST");
      window.location.hash = "#workbench";
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "退出失败，请重试");
      setBusy(false);
    }
  }

  if (!account || account.local) return null;
  return <section className="wb-account" aria-label="当前账户">
    <span>当前账户：<strong>{account.email}</strong> · 课程和资料按账户保存</span>
    <button type="button" disabled={busy} onClick={switchAccount}>{busy ? "正在退出…" : "切换账户"}</button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
