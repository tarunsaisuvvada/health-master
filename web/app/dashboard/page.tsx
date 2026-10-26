"use client";

import { useEffect, useState } from "react";

type Day = { date: string; steps?: number; sleep_hours?: number; avg_hr?: number; min_hr?: number; active_minutes?: number };
type Health = { profile: string; days: Day[]; errors: string[] };

const show = (v: number | undefined, unit = "") => (v === undefined ? "–" : `${v.toLocaleString()}${unit}`);

async function errorText(r: Response) {
  const body = await r.json().catch(() => null);
  return body?.detail ?? `Request failed (${r.status})`;
}

export default function Dashboard() {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [profile, setProfile] = useState("");
  const [days, setDays] = useState(7);
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/me")
      .then(async (r) => {
        if (r.status === 401) return (window.location.href = "/");
        if (!r.ok) return setError(await errorText(r));
        const me = await r.json();
        setProfiles(me.profiles);
        setProfile(new URLSearchParams(window.location.search).get("profile") || me.email);
      })
      .catch(() => setError("Cannot reach the backend. Is it running on port 8000?"));
  }, []);

  useEffect(() => {
    if (!profile) return;
    let stale = false;
    setData(null);
    setError("");
    fetch(`/api/health?profile=${encodeURIComponent(profile)}&days=${days}`)
      .then(async (r) => {
        const result = r.ok ? { data: await r.json() } : { error: await errorText(r) };
        if (stale) return;
        if (result.data) setData(result.data);
        else setError(result.error);
      })
      .catch(() => !stale && setError("Cannot reach the backend. Is it running on port 8000?"));
    return () => {
      stale = true;
    };
  }, [profile, days]);

  async function logout() {
    await fetch("/auth/logout", { method: "POST" });
    window.location.href = "/";
  }

  const today = data?.days.at(-1);

  return (
    <>
      <header>
        <h1 style={{ margin: 0 }}>Parent Health</h1>
        <div className="controls">
          <select aria-label="Account" value={profile} onChange={(e) => setProfile(e.target.value)}>
            {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select aria-label="Range" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
          <a className="btn ghost" href="/auth/login">+ Add account</a>
          <button className="btn ghost" onClick={logout}>Log out</button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <>
          <div className="grid">
            <div className="card stat"><div className="label muted">Steps today</div><div className="value">{show(today?.steps)}</div></div>
            <div className="card stat"><div className="label muted">Sleep last night</div><div className="value">{show(today?.sleep_hours, " h")}</div></div>
            <div className="card stat"><div className="label muted">Lowest heart rate</div><div className="value">{show(today?.min_hr, " bpm")}</div></div>
            <div className="card stat"><div className="label muted">Active minutes</div><div className="value">{show(today?.active_minutes)}</div></div>
          </div>

          <div className="card table-wrap">
            <table>
              <thead><tr><th>Date</th><th>Steps</th><th>Sleep (h)</th><th>Avg HR</th><th>Low HR</th><th>Active min</th></tr></thead>
              <tbody>
                {[...data.days].reverse().map((d) => (
                  <tr key={d.date}>
                    <td>{d.date}</td><td>{show(d.steps)}</td><td>{show(d.sleep_hours)}</td>
                    <td>{show(d.avg_hr)}</td><td>{show(d.min_hr)}</td><td>{show(d.active_minutes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.errors.length > 0 && (
            <div className="card" style={{ marginTop: 20 }}>
              <strong>Some data could not be loaded</strong>
              {data.errors.map((e) => <p key={e} className="error">{e}</p>)}
            </div>
          )}
        </>
      )}
    </>
  );
}
