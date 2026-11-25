import assert from "node:assert/strict";
import test from "node:test";
import {
  initializeMsg91Widget, retryMsg91Otp, sendMsg91Otp, verifyMsg91Otp,
} from "../src/api/msg91Widget.js";
import { responseErrorMessage, responseMessage, responseRequestId } from "../src/otp.js";

test("initializes the official Web widget with its documented custom-UI configuration", async () => {
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  let observedConfiguration;
  globalThis.window = {
    initSendOTP: (configuration) => { observedConfiguration = configuration; },
  };
  globalThis.document = {
    createElement: () => ({ remove() {} }),
    head: { appendChild: (script) => script.onload() },
  };

  try {
    await initializeMsg91Widget("test-widget-id", "test-token-auth");
    assert.equal(observedConfiguration.widgetId, "test-widget-id");
    assert.equal(observedConfiguration.tokenAuth, "test-token-auth");
    assert.equal(observedConfiguration.exposeMethods, true);
    assert.equal(observedConfiguration.captchaRenderId, "msg91-captcha");
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});

test("calls the documented Web SDK method signatures for send, retry and verify", async () => {
  const originalWindow = globalThis.window;
  const calls = [];
  globalThis.window = {
    sendOtp: (...args) => { calls.push(["sendOtp", ...args.slice(0, 1)]); args[1]({ request_id: "request-1" }); },
    retryOtp: (...args) => { calls.push(["retryOtp", args[0], args[3]]); args[1]({ request_id: "request-2" }); },
    verifyOtp: (...args) => { calls.push(["verifyOtp", args[0], args[3]]); args[1]("verified-token"); },
  };

  try {
    assert.deepEqual(await sendMsg91Otp("919800000001"), { request_id: "request-1" });
    assert.deepEqual(await retryMsg91Otp("request-1"), { request_id: "request-2" });
    assert.equal(await verifyMsg91Otp("123456", "request-2"), "verified-token");
    assert.deepEqual(calls, [
      ["sendOtp", "919800000001"],
      ["retryOtp", "11", "request-1"],
      ["verifyOtp", "123456", "request-2"],
    ]);
  } finally {
    globalThis.window = originalWindow;
  }
});

test("reads MSG91 request_id from a successful send response", () => {
  assert.equal(responseRequestId({
    type: "success",
    request_id: "request-123",
  }), "request-123");
});

test("reads request IDs returned in supported nested response shapes", () => {
  assert.equal(responseRequestId({
    type: "success",
    data: { reqId: "request-456" },
  }), "request-456");
});

test("supports the Widget response where message contains the request ID", () => {
  assert.equal(responseRequestId({
    type: "success",
    message: "request-789",
  }), "request-789");
});

test("accepts a successful status response", () => {
  assert.equal(responseRequestId({
    status: "success",
    data: { requestId: "request-987" },
  }), "request-987");
});

test("reads the verified access token from the MSG91 response message", () => {
  assert.equal(responseMessage({
    type: "success",
    message: "verified-access-token",
  }), "verified-access-token");
});

test("does not accept an error response as a successful OTP operation", () => {
  assert.equal(responseRequestId({ type: "error", message: "Rejected" }), null);
  assert.equal(responseMessage({ type: "error", message: "Rejected" }), null);
});

test("exposes a provider error message for troubleshooting", () => {
  assert.equal(responseErrorMessage({
    type: "error",
    message: "Widget domain is not allowed",
  }), "Widget domain is not allowed");
});
