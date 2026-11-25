import { useState } from "react";
import Brand from "../components/Brand.jsx";
import { demoLogin } from "../api/recommendations.js";

// Sign in with a customer ID exactly as it is stored in Supabase (customers.customer_id).
export default function LoginPage({ onLogin }) {
  const [customerId, setCustomerId] = useState("");
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  async function submit(e) {
    e.preventDefault();
    const id = customerId.trim();
    if (!id) {
      setError("Enter your customer ID.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const { access_token, customer_id } = await demoLogin(id);
      onLogin({ token: access_token, customerId: customer_id });
    } catch (err) {
      setError(err.code === "customer_not_found"
        ? `No customer with ID “${id}”. IDs are case-sensitive, e.g. CUST-0001.`
        : err.message);
      setPending(false);
    }
  }

  return (
    <div className="login">
      <header className="login__top"><Brand /></header>

      <section className="login__hero">
        <p className="eyebrow">Personalised for every household</p>
        <h1>Never run out of the <span className="accent">everyday essentials</span>.</h1>
        <p className="lede">
          We learn how often you buy milk, rice or toothpaste and remind you before you run out.
          Subscribe once, approve each delivery on WhatsApp.
        </p>
      </section>

      <form className="login__panel" onSubmit={submit} noValidate>
        <h2>Sign in</h2>
        <p className="muted">Enter your customer ID to see your top 10 reorder picks.</p>

        <label className="field" htmlFor="customer-id">
          <span className="field__label">Customer ID</span>
          <input id="customer-id" className="input" value={customerId} placeholder="e.g. CUST-0001"
                 autoComplete="username" autoFocus spellCheck={false}
                 aria-invalid={!!error} aria-describedby={error ? "login-error" : undefined}
                 onChange={(e) => { setCustomerId(e.target.value); setError(null); }} />
        </label>

        {error && <p className="form-error" id="login-error" role="alert">{error}</p>}

        <button type="submit" className="btn btn--primary btn--block" disabled={pending}>
          {pending ? "Signing in…" : "See my recommendations"}
        </button>
        <p className="login__hint">Demo sign-in: no password needed.</p>
      </form>
    </div>
  );
}
