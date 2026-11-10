(() => {
  const card = document.getElementById("voucher-card");
  const message = document.getElementById("voucher-sync");

  if (!card || !message) return;

  let busy = false;
  let stopped = false;

  function showMessage(text) {
    if (message.textContent !== text) {
      message.textContent = text;
    }
  }

  async function checkStatus() {
    if (busy || stopped || document.hidden) return;

    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);

    try {
      const response = await fetch(card.dataset.statusUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal
      });

      if ([401, 403, 404].includes(response.status)) {
        stopped = true;
        clearInterval(timer);

        document.getElementById("voucher-presentation").hidden = true;
        document.getElementById("voucher-state").textContent =
          "Voucher unavailable";

        showMessage(
          "Your account can no longer access this voucher. Sign in again."
        );
        return;
      }

      if (!response.ok) throw new Error("Status request failed");

      const result = await response.json();

      if (!["ready", "redeemed", "expired"].includes(result.status)) {
        throw new Error("Unexpected voucher status");
      }

      if (result.status !== card.dataset.state) {
        stopped = true;
        clearInterval(timer);

        // Render the latest status and redemption time from the server.
        window.location.reload();
        return;
      }

      showMessage("Status updates automatically while this page is open.");
    } catch {
      showMessage(
        "Automatic status checks are unavailable. Ask staff to check your code."
      );
    } finally {
      clearTimeout(timeout);
      busy = false;
    }
  }

  const timer = setInterval(checkStatus, 5000);

  document.addEventListener("visibilitychange", checkStatus);
  checkStatus();
})();