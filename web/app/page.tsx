export default async function Login({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return (
    <div className="card login">
      <h1 style={{ marginTop: 0 }}>Parent Health</h1>
      <p className="muted">Sign in with the Google account that has the Fit data.</p>
      <a className="btn" href="/auth/login">Sign in with Google</a>
      {error && <p className="error">Sign-in failed: {error}</p>}
    </div>
  );
}
