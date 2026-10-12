document.querySelectorAll(".notification").forEach((notification) => {
  const closeButton = notification.querySelector(".notification-close");

  const dismiss = () => {
    notification.classList.add("notification-dismissed");
    notification.addEventListener("transitionend", () => notification.remove(), {
      once: true,
    });
    window.setTimeout(() => notification.remove(), 300);
  };

  closeButton?.addEventListener("click", dismiss);

  if (notification.classList.contains("notification-success")) {
    window.setTimeout(dismiss, 7000);
  }
});
