(() => {
  const openButton = document.getElementById("open-voucher-scanner");
  const message = document.getElementById("scanner-message");
  const input = document.getElementById("voucher-code-input");

  if (!openButton || !message || !input) return;

  let scanner = null;
  let captured = false;

  openButton.addEventListener("click", () => {
    if (scanner) return;

    if (typeof Html5QrcodeScanner === "undefined") {
      message.textContent =
        "The scanner could not load. Enter the voucher code manually.";
      return;
    }

    captured = false;
    openButton.disabled = true;

    message.textContent = window.isSecureContext
      ? "Choose a camera or scan an image of the customer's QR code."
      : "Camera access requires HTTPS or localhost. Use image scanning or enter the code.";

    scanner = new Html5QrcodeScanner(
      "voucher-reader",
      {
        fps: 10,
        qrbox: { width: 200, height: 200 },
        formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
        rememberLastUsedCamera: false
      },
      false
    );

    scanner.render(
      async (decodedText) => {
        if (captured) return;

        const code = decodedText.trim().toUpperCase();

        if (!/^KC-[0-9A-F]{12}$/.test(code)) {
          message.textContent =
            "This is not a recognised PerkFi voucher. Scan another QR code.";
          return;
        }

        captured = true;
        input.value = code;

        try {
          await scanner.clear();
          scanner = null;
          openButton.disabled = false;

          message.textContent =
            "Code captured. Select Check voucher to validate it.";
        } catch {
          message.textContent =
            "Code captured. Stop scanning, then select Check voucher.";
        }

        input.focus();
      },
      () => {
        // A frame without a readable QR is normal; keep scanning.
      }
    );
  });

  input.addEventListener("input", () => {
    input.value = input.value.toUpperCase();
  });
})();