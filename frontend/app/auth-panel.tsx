"use client";

import { createClient, type Session } from "@supabase/supabase-js";
import { useEffect, useState, type FormEvent } from "react";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
const supabase = url && key ? createClient(url, key) : null;

export default function AuthPanel({ onSession }: { onSession: (session: Session | null) => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      onSession(nextSession);
      setMessage("");
    });
    return () => data.subscription.unsubscribe();
  }, [onSession]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setMessage("");
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      setPassword("");
      if (error) setMessage("Sign-in failed. Check email and password, then try again.");
    } catch {
      setMessage("Sign-in unavailable. Check connection and try again.");
    } finally { setBusy(false); }
  }

  async function signOut() {
    if (!supabase) return;
    setBusy(true);
    try {
      const { error } = await supabase.auth.signOut();
      if (error) setMessage("Sign-out failed. Try again.");
    } catch { setMessage("Sign-out unavailable. Try again."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="account-title" className="account-panel">
      <h2 id="account-title" className="mb-3 text-xl font-semibold">Your account</h2>
      {!supabase ? <p>Sign-in unavailable. Local setup instructions are in README.</p> : session ? (
        <div className="space-y-4">
          <p>Signed in as {session.user.email}</p>
          <button type="button" onClick={signOut} disabled={busy} className="min-h-11 rounded-lg border border-emerald-800 px-5 py-3 disabled:opacity-60">Sign out</button>
        </div>
      ) : (
        <form onSubmit={signIn} className="flex max-w-md flex-col gap-4">
          <p>Sign in with your existing account. Your sales data stays private.</p>
          <label className="flex flex-col gap-1">Email<input type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} className="min-h-11 rounded-lg border border-emerald-900/30 px-3" /></label>
          <label className="flex flex-col gap-1">Password<input type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} className="min-h-11 rounded-lg border border-emerald-900/30 px-3" /></label>
          <button disabled={busy} className="min-h-11 rounded-lg bg-emerald-800 px-5 py-3 font-semibold text-white disabled:opacity-60">{busy ? "Signing in..." : "Sign in"}</button>
        </form>
      )}
      <p role="status" className="mt-4">{message}</p>
    </section>
  );
}
