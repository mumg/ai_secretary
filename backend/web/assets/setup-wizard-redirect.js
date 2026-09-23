fetch("/api/v1/admin/setup-wizard", { cache: "no-store" })
  .then(response => response.ok ? response.json() : null)
  .then(state => { if (state?.required) location.replace("/admin?setup=1"); })
  .catch(() => {});
