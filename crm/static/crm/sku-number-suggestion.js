(() => {
  window.CrmSkuNumberSuggestion = {
    isValid(hint, requirePrefix = false) {
      if (typeof hint?.suggested_code !== "string" || !hint.suggested_code.trim()) return false;
      if (!requirePrefix) return true;
      return typeof hint.sku_prefix === "string"
        && hint.sku_prefix.length > 0
        && hint.suggested_code.startsWith(hint.sku_prefix)
        && /^\d+$/.test(hint.suggested_code.slice(hint.sku_prefix.length));
    },
  };

  function init() {
    const product = document.getElementById("id_product");
    const code = document.getElementById("id_code");
    if (!product || !code || code.readOnly) return;

    const row = code.closest(".form-row") || code.parentElement;
    const hint = document.createElement("p");
    const button = document.createElement("button");
    let request = 0;
    let suggestion = null;

    hint.setAttribute("role", "status");
    hint.setAttribute("aria-live", "polite");
    button.type = "button";
    row.append(hint, button);

    function render() {
      if (!suggestion) return;
      if (code.value.trim()) {
        hint.textContent = `已填写编号：${code.value.trim()}（建议不会覆盖）`;
        button.hidden = true;
        return;
      }
      hint.textContent = `当前最大号：${suggestion.current_max_code || "暂无"}；下一号建议：${suggestion.suggested_code}（未预占）`;
      button.textContent = "使用下一号建议";
      button.hidden = false;
      button.onclick = () => {
        if (code.value.trim()) return;
        code.value = suggestion.suggested_code;
        code.dispatchEvent(new Event("input", { bubbles: true }));
        code.dispatchEvent(new Event("change", { bubbles: true }));
      };
    }

    async function load() {
      const current = ++request;
      const productId = product.value;
      suggestion = null;
      button.hidden = true;
      hint.textContent = productId ? "正在读取编号建议…" : "选择商品后显示编号建议。";
      if (!productId) return;
      try {
        const response = await fetch(`/api/v1/skus/number-suggestion?product_id=${encodeURIComponent(productId)}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }).catch(() => { throw new Error("网络请求失败，请检查网络后重试。"); });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.error?.message || `读取失败（HTTP ${response.status}）。`);
        if (!window.CrmSkuNumberSuggestion.isValid(data)) throw new Error("服务器返回的编号建议无效，请重试。");
        if (current !== request || product.value !== productId) return;
        suggestion = data;
        render();
      } catch (error) {
        if (current !== request || product.value !== productId) return;
        hint.textContent = `编号建议加载失败：${error.message || "请重试。"}`;
        button.textContent = "重试编号建议";
        button.hidden = false;
        button.onclick = load;
      }
    }

    product.addEventListener("change", load);
    code.addEventListener("input", render);
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
