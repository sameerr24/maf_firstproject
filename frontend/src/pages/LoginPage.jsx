import { useEffect, useState } from "react";
import { parsePhoneNumberFromString } from "libphonenumber-js/min";
import Brand from "../components/Brand.jsx";
import { getWidgetConfig, phoneLogin, registerCustomer } from "../api/recommendations.js";
import {
  initializeMsg91Widget, retryMsg91Otp, sendMsg91Otp, verifyMsg91Otp,
} from "../api/msg91Widget.js";
import { responseErrorMessage, responseMessage, responseRequestId } from "../otp.js";

const COUNTRIES = [
  ["IN", "India (+91)"], ["US", "United States (+1)"], ["GB", "United Kingdom (+44)"],
  ["CA", "Canada (+1)"], ["AU", "Australia (+61)"],
];
const RESEND_SECONDS = 30;

export default function LoginPage({ onLogin, initialError = null }) {
  const [mode, setMode] = useState("login");
  const [phone, setPhone] = useState("");
  const [region, setRegion] = useState("IN");
  const [customerName, setCustomerName] = useState("");
  const [otp, setOtp] = useState("");
  const [requestId, setRequestId] = useState(null);
  const [normalizedPhone, setNormalizedPhone] = useState(null);
  const [secondsUntilResend, setSecondsUntilResend] = useState(0);
  const [widgetReady, setWidgetReady] = useState(false);
  const [widgetError, setWidgetError] = useState(null);
  const [error, setError] = useState(initialError);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    getWidgetConfig()
      .then(async ({ widget_id, token_auth }) => {
        await initializeMsg91Widget(widget_id, token_auth);
        if (active) setWidgetReady(true);
      })
      .catch(() => {
        if (active) setWidgetError("Phone sign-in is temporarily unavailable. Please try again later.");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (secondsUntilResend <= 0) return undefined;
    const timer = window.setTimeout(() => setSecondsUntilResend((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [secondsUntilResend]);

  function resetOtp() {
    setRequestId(null);
    setNormalizedPhone(null);
    setOtp("");
    setSecondsUntilResend(0);
  }

  function switchMode(nextMode) {
    setMode(nextMode);
    setError(null);
    resetOtp();
  }

  function normalizedInput() {
    const parsed = parsePhoneNumberFromString(phone.trim(), region);
    if (!parsed || !parsed.isValid()) throw new Error("Enter a valid phone number for the selected country.");
    return parsed;
  }

  async function sendOtp(event) {
    event.preventDefault();
    setError(null);
    let parsed;
    try {
      parsed = normalizedInput();
    } catch (validationError) {
      setError(validationError.message);
      return;
    }
    if (!widgetReady) {
      setError(widgetError ?? "Connecting to the OTP service. Please wait a moment.");
      return;
    }

    setPending(true);
    try {
      const result = await sendMsg91Otp(parsed.number.replace(/\D/g, ""));
      const id = responseRequestId(result);
      if (!id) {
        const providerError = responseErrorMessage(result);
        throw new Error(providerError
          ? `MSG91 rejected the OTP request: ${providerError}`
          : "MSG91 returned no OTP request ID. Check the widget setup and allowed domains.");
      }
      setRequestId(id);
      setNormalizedPhone(parsed.number);
      setOtp("");
      setSecondsUntilResend(RESEND_SECONDS);
    } catch (sendError) {
      setError(responseErrorMessage(sendError) ?? (sendError instanceof Error && sendError.message
        ? sendError.message
        : "Couldn't send a code. Please try again."));
    } finally {
      setPending(false);
    }
  }

  async function resendOtp() {
    setError(null);
    setPending(true);
    try {
      const result = await retryMsg91Otp(requestId);
      const nextRequestId = responseRequestId(result);
      if (!nextRequestId) {
        const providerError = responseErrorMessage(result);
        throw new Error(providerError
          ? `MSG91 couldn't resend the code: ${providerError}`
          : "MSG91 returned no request ID for the resend. Check the widget setup.");
      }
      setRequestId(nextRequestId);
      setOtp("");
      setSecondsUntilResend(RESEND_SECONDS);
    } catch (resendError) {
      setError(responseErrorMessage(resendError) ?? (resendError instanceof Error && resendError.message
        ? resendError.message
        : "Couldn't resend the code. Please try again."));
    } finally {
      setPending(false);
    }
  }

  async function verifyOtp(event) {
    event.preventDefault();
    setError(null);
    if (!/^\d{4,8}$/.test(otp)) {
      setError("Enter the OTP sent to your phone.");
      return;
    }
    setPending(true);
    try {
      const verification = await verifyMsg91Otp(otp, requestId);
      const msg91AccessToken = responseMessage(verification);
      if (!msg91AccessToken) throw new Error("That code is invalid or expired. Request a new OTP.");

      const credentials = {
        msg91_access_token: msg91AccessToken,
        phone_number: normalizedPhone,
        region,
      };
      const result = mode === "register"
        ? await registerCustomer({
          ...credentials,
          customer_name: customerName.trim() || null,
        })
        : await phoneLogin(credentials);
      onLogin({
        token: result.access_token,
        customerId: result.customer_id,
        customerName: result.customer_name,
      });
    } catch (verifyError) {
      if (verifyError.code === "phone_not_registered") {
        setError("No account is linked to this number yet. Choose “Create account” to register.");
      } else if (verifyError.code === "invalid_verification") {
        setError("The code could not be verified. Check it or request a new OTP.");
      } else if (verifyError.code === "rate_limited") {
        setError("Too many attempts. Please wait before trying again.");
      } else if (verifyError.code === "service_unavailable") {
        setError("Authentication is temporarily unavailable. Please try again later.");
      } else {
        setError(verifyError.message || "Sign-in failed. Please try again.");
      }
    } finally {
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

      <form className="login__panel" onSubmit={requestId ? verifyOtp : sendOtp} noValidate>
        <h2>{mode === "register" ? "Create your account" : "Sign in"}</h2>
        <p className="muted">
          {requestId
            ? `Enter the code sent to ${normalizedPhone}.`
            : "Continue with the phone number registered to your account."}
        </p>

        {!requestId && (
          <>
            <label className="field" htmlFor="phone-number">
              <span className="field__label">Phone number</span>
              <div className="phone-input">
                <select id="phone-region" className="input phone-input__country" value={region}
                        aria-label="Country" onChange={(event) => setRegion(event.target.value)}>
                  {COUNTRIES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                </select>
                <input id="phone-number" className="input" type="tel" value={phone}
                       placeholder="Enter phone number" autoComplete="tel" autoFocus
                       aria-invalid={!!error} aria-describedby={error ? "login-error" : undefined}
                       onChange={(event) => { setPhone(event.target.value); setError(null); }} />
              </div>
            </label>

            {mode === "register" && (
              <>
                <label className="field" htmlFor="customer-name">
                  <span className="field__label">Name <span className="muted">(optional)</span></span>
                  <input id="customer-name" className="input" value={customerName} maxLength={100}
                         autoComplete="name" onChange={(event) => setCustomerName(event.target.value)} />
                </label>
              </>
            )}
          </>
        )}

        {requestId && (
          <>
            <label className="field" htmlFor="otp-code">
              <span className="field__label">One-time code</span>
              <input id="otp-code" className="input otp-input" type="text" inputMode="numeric"
                     autoComplete="one-time-code" maxLength={8} value={otp} autoFocus
                     aria-invalid={!!error} aria-describedby={error ? "login-error" : undefined}
                     onChange={(event) => { setOtp(event.target.value.replace(/\D/g, "")); setError(null); }} />
            </label>
            <button type="button" className="link-btn" disabled={pending || secondsUntilResend > 0}
                    onClick={resendOtp}>
              {secondsUntilResend > 0 ? `Resend code in ${secondsUntilResend}s` : "Resend code"}
            </button>
            <button type="button" className="link-btn" onClick={resetOtp} disabled={pending}>
              Change phone number
            </button>
          </>
        )}

        {(error || widgetError) && (
          <p className="form-error" id="login-error" role="alert">{error ?? widgetError}</p>
        )}

        <div id="msg91-captcha" />

        <button type="submit" className="btn btn--primary btn--block" disabled={pending || (!requestId && !widgetReady)}>
          {pending ? "Please wait…" : requestId ? "Verify and continue" : "Send OTP"}
        </button>

        {!requestId && (
          <p className="login__hint">
            {mode === "register" ? "Already registered? " : "New to Smart Reorder? "}
            <button type="button" className="link-btn" disabled={pending}
                    onClick={() => switchMode(mode === "register" ? "login" : "register")}>
              {mode === "register" ? "Sign in" : "Create account"}
            </button>
          </p>
        )}
      </form>
    </div>
  );
}
