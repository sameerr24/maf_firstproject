const SCRIPT_URLS = [
  "https://verify.msg91.com/otp-provider.js",
  "https://verify.phone91.com/otp-provider.js",
];

let initialization;

export function initializeMsg91Widget(widgetId, tokenAuth) {
  if (!widgetId || !tokenAuth) {
    return Promise.reject(new Error("MSG91 Widget configuration is missing."));
  }
  if (initialization) return initialization;

  initialization = new Promise((resolve, reject) => {
    function loadScript(index) {
      if (index >= SCRIPT_URLS.length) {
        reject(new Error("Couldn't load the MSG91 Web Widget."));
        return;
      }

      const script = document.createElement("script");
      script.src = SCRIPT_URLS[index];
      script.async = true;
      script.onload = () => {
        if (typeof window.initSendOTP !== "function") {
          script.remove();
          loadScript(index + 1);
          return;
        }

        try {
          window.initSendOTP({
            widgetId,
            tokenAuth,
            exposeMethods: true,
            captchaRenderId: "msg91-captcha",
            success: () => {},
            failure: () => {},
          });
          resolve();
        } catch (error) {
          reject(error);
        }
      };
      script.onerror = () => {
        script.remove();
        loadScript(index + 1);
      };
      document.head.appendChild(script);
    }

    loadScript(0);
  }).catch((error) => {
    initialization = null;
    throw error;
  });

  return initialization;
}

function callWidgetMethod(name, leadingArgs, trailingArgs = []) {
  return new Promise((resolve, reject) => {
    const method = window[name];
    if (typeof method !== "function") {
      reject(new Error("MSG91 Web Widget methods are not initialized."));
      return;
    }
    method(...leadingArgs, resolve, reject, ...trailingArgs);
  });
}

export function sendMsg91Otp(identifier) {
  return callWidgetMethod("sendOtp", [identifier]);
}

export function retryMsg91Otp(requestId) {
  return callWidgetMethod("retryOtp", ["11"], [requestId]);
}

export function verifyMsg91Otp(otp, requestId) {
  return callWidgetMethod("verifyOtp", [otp], [requestId]);
}
