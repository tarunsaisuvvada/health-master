const API = process.env.API_URL || "http://localhost:8000";

// Browser only talks to Next; /api and /auth are proxied so the session cookie stays first-party.
export default {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/auth/:path*", destination: `${API}/auth/:path*` },
    ];
  },
};
