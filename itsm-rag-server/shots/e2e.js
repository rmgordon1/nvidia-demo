
// Same-origin E2E driver (temporarily copied into the container, never part of the app build).
const p = new URLSearchParams(location.search), v = p.get("v"), wait = +(p.get("wait") || 40);
const f = document.getElementById("f");
localStorage.setItem("bep.role", JSON.stringify(p.get("role") || "admin"));
f.src = `/#/${v}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { const x = fn(); if (x) return x; await sleep(200); } }
f.onload = async () => {
  const d = () => f.contentDocument;
  if (v === "insights") {
    const inp = await until(() => d().querySelector(".ins-ask input"));
    inp.value = "MTTR in hours for MES Support by month in 2026"; d().querySelector(".ins-ask").requestSubmit();
  } else if (v === "itsm") {
    const ta = await until(() => d().querySelector(".itsm-intake textarea"));
    ta.value = "Intaglio press HMI on line 3 is frozen, operator can't load the next job. Touchscreen not responding after shift change.";
    d().querySelector(".itsm-intake").requestSubmit();
    await until(() => d().querySelector(".itsm-steps"), 60000);
    const ci = d().querySelector(".itsm-chat textarea"); ci.value = "Power cycle didn't help. What next, and who owns the PLC side?";
    d().querySelector(".itsm-chat").requestSubmit();
  } else if (v === "help") {
    const ta = await until(() => d().querySelector(".ss-chat textarea"));
    await until(() => d().querySelector(".ss-mine .ss-ticket"));
    ta.value = "I clicked a link in an email about my paycheck and typed my password before I realized"; d().querySelector(".ss-chat").requestSubmit();
    await until(() => d().querySelector(".ss-offer button.btn.primary"), 60000);
    d().querySelector(".ss-offer button.btn.primary").click();
  }
};
