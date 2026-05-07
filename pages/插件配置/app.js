const mockConfig = {
    permission_config: { allowed_users: "", blocked_users: "", unlimited_groups: "" },
    usage_config: {
        enable_daily_limit: false,
        daily_image_limit: 20,
        enable_checkin: false,
        checkin_bonus_min: 1,
        checkin_bonus_max: 3
    },
    persona_config: {
        active_persona_id: "default",
        persona_name: "默认助理",
        persona_base_prompt: "",
        persona_ref_image: [],
        profiles: [
            { id: "default", persona_name: "默认助理", persona_base_prompt: "", persona_ref_image: [] }
        ]
    },
    optimizer_config: {
        enable_optimizer: true,
        optimizer_style: "自拍专用极致真实",
        chain_optimizer: "node_1",
        optimizer_model: "gpt-4o-mini",
        optimizer_timeout: 15,
        max_batch_count: 0,
        optimizer_custom_prompt: ""
    },
    router_config: { chain_text2img: "node_1", chain_selfie: "node_1", chain_video: "video_node_1" },
    presets: ["写真:daily smartphone portrait --size 1024x1024"],
    providers: [
        { id: "node_1", api_type: "openai_image", base_url: "https://api.example.com/v1", model: "gpt-image-1", available_models: ["gpt-image-1", "dall-e-3"], timeout: 60, api_keys: "" }
    ],
    video_providers: [
        { id: "video_node_1", api_type: "async_task", base_url: "https://api.example.com/v1", model: "veo", available_models: ["veo"], timeout: 300, api_keys: "" }
    ],
    verbose_report: false
};

const mockUsageStats = {
    date: new Date().toISOString().slice(0, 10),
    total: 8,
    users: [
        { user_id: "10001", display_name: "10001", count: 5, bonus: 2, checkin_at: Math.floor(Date.now() / 1000) - 2400, last_at: Math.floor(Date.now() / 1000) - 1800, access_level: "limited" },
        { user_id: "10002", display_name: "10002", count: 3, bonus: 0, checkin_at: 0, last_at: Math.floor(Date.now() / 1000) - 7200, access_level: "unlimited_user" }
    ],
    quota: { enabled: false, daily_limit: 0 }
};

const bridge = window.AstrBotPluginPage || {
    ready: async () => ({}),
    apiGet: async (name) => JSON.parse(JSON.stringify(name === "get_usage_stats" ? { success: true, stats: mockUsageStats } : mockConfig)),
    apiPost: async (_, payload) => {
        console.info("[OmniDraw local preview] save_config", payload);
        return { success: true };
    }
};

let state = {
    permission_config: { allowed_users: "", blocked_users: "", unlimited_groups: "" },
    usage_config: {
        enable_daily_limit: false,
        daily_image_limit: 20,
        enable_checkin: false,
        checkin_bonus_min: 1,
        checkin_bonus_max: 3
    },
    usage_stats: { date: "", total: 0, users: [], quota: { enabled: false, daily_limit: 0 } },
    persona_config: { active_persona_id: "default", profiles: [], persona_ref_image: [] },
    optimizer_config: {},
    router_config: {},
    presets: [],
    providers: [],
    video_providers: [],
    verbose_report: false
};

let initialized = false;
let savedSnapshot = "";

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[char]));
}

function parsePreset(rawPreset) {
    if (typeof rawPreset === "object" && rawPreset !== null) {
        return { name: rawPreset.name || "", prompt: rawPreset.prompt || "" };
    }
    const text = String(rawPreset || "");
    const idx = text.indexOf(":");
    if (idx === -1) return { name: text, prompt: "" };
    return { name: text.slice(0, idx), prompt: text.slice(idx + 1) };
}

const deepFind = (obj, keys, def = "") => {
    if (!obj) return def;
    for (const key of keys) {
        if (obj[key] !== undefined) return obj[key];
    }
    return def;
};

const byId = (id) => document.getElementById(id);

function normalizeModelList(value) {
    const source = Array.isArray(value) ? value : String(value || "").split(",");
    return [...new Set(source.map((item) => String(item).trim()).filter(Boolean))];
}

function normalizeTextAreaKeys(value) {
    return Array.isArray(value) ? value.join("\n") : String(value || "");
}

function readNonnegativeIntInput(id, fallback = 0) {
    const parsed = parseInt(byId(id)?.value, 10);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function normalizeIdText(value) {
    const source = Array.isArray(value)
        ? value.flatMap((item) => String(item || "").split(/[\s,]+/))
        : String(value || "").split(/[\s,]+/);
    return [...new Set(source.map((item) => String(item).trim()).filter(Boolean))].join("\n");
}

function mergeIdText(...values) {
    return normalizeIdText(values.flatMap((value) => normalizeIdText(value).split("\n")));
}

function normalizePermissionConfig(value = {}) {
    return {
        allowed_users: mergeIdText(value.allowed_users, value.unlimited_users, value.user_whitelist),
        blocked_users: mergeIdText(value.blocked_users, value.user_blacklist),
        unlimited_groups: mergeIdText(value.unlimited_groups, value.group_whitelist)
    };
}

function normalizeUsageConfig(value = {}) {
    const limit = parseInt(value.daily_image_limit ?? 20, 10);
    const bonusMin = parseInt(value.checkin_bonus_min ?? 1, 10);
    const bonusMax = parseInt(value.checkin_bonus_max ?? 3, 10);
    const normalizedMin = Number.isFinite(bonusMin) && bonusMin >= 0 ? bonusMin : 1;
    const normalizedMax = Number.isFinite(bonusMax) && bonusMax >= 0 ? bonusMax : 3;
    return {
        enable_daily_limit: Boolean(value.enable_daily_limit),
        daily_image_limit: Number.isFinite(limit) && limit > 0 ? limit : 20,
        enable_checkin: Boolean(value.enable_checkin),
        checkin_bonus_min: normalizedMin,
        checkin_bonus_max: Math.max(normalizedMin, normalizedMax)
    };
}

function normalizeUsageStats(value = {}) {
    const stats = value.stats || value;
    const rawUsers = Array.isArray(stats.users)
        ? stats.users
        : Object.entries(stats.users || {}).map(([userId, record]) => ({ user_id: userId, ...(record || {}) }));
    const users = rawUsers
        .map((user) => ({
            user_id: String(user.user_id || user.id || "").trim(),
            display_name: String(user.display_name || user.name || "").trim(),
            count: parseInt(user.count || 0, 10) || 0,
            bonus: parseInt(user.bonus || 0, 10) || 0,
            checkin_at: parseInt(user.checkin_at || 0, 10) || 0,
            group_id: String(user.group_id || "").trim(),
            access_level: String(user.access_level || "limited").trim(),
            last_at: parseInt(user.last_at || 0, 10) || 0
        }))
        .filter((user) => user.user_id)
        .sort((a, b) => b.count - a.count || a.user_id.localeCompare(b.user_id));
    const total = Number.isFinite(parseInt(stats.total, 10))
        ? parseInt(stats.total, 10)
        : users.reduce((sum, user) => sum + user.count, 0);
    return {
        date: stats.date || "",
        total,
        users,
        quota: {
            enabled: Boolean(stats.quota?.enabled),
            daily_limit: parseInt(stats.quota?.daily_limit || 0, 10) || 0,
            checkin_enabled: Boolean(stats.quota?.checkin_enabled),
            checkin_bonus_min: parseInt(stats.quota?.checkin_bonus_min || 0, 10) || 0,
            checkin_bonus_max: parseInt(stats.quota?.checkin_bonus_max || 0, 10) || 0
        }
    };
}

function formatUsageTime(timestamp) {
    if (!timestamp) return "—";
    return new Date(timestamp * 1000).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit"
    });
}

function normalizePersonaImages(value) {
    if (typeof value === "string" && value.trim()) return [value];
    if (Array.isArray(value)) return value.filter(Boolean);
    return [];
}

function makePersonaId(seed, fallbackIndex = 1) {
    const ascii = String(seed || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "_")
        .replace(/^_+|_+$/g, "");
    return ascii || `persona_${fallbackIndex}`;
}

function uniquePersonaId(seed, index, usedIds) {
    const base = makePersonaId(seed, index + 1);
    let candidate = base;
    let suffix = 2;
    while (usedIds.has(candidate)) {
        candidate = `${base}_${suffix}`;
        suffix += 1;
    }
    usedIds.add(candidate);
    return candidate;
}

function normalizePersonaProfiles(rawPersonaConfig = {}) {
    const rawProfiles = Array.isArray(rawPersonaConfig.profiles) && rawPersonaConfig.profiles.length
        ? rawPersonaConfig.profiles
        : [{
            id: rawPersonaConfig.active_persona_id || rawPersonaConfig.persona_id || "default",
            persona_name: rawPersonaConfig.persona_name || "默认助理",
            persona_base_prompt: rawPersonaConfig.persona_base_prompt || "",
            persona_ref_image: rawPersonaConfig.persona_ref_image || rawPersonaConfig.persona_ref_images || []
        }];

    const usedIds = new Set();
    const profiles = rawProfiles.map((profile, index) => {
        const source = profile && typeof profile === "object" ? profile : {};
        const name = String(source.persona_name || source.name || (index === 0 ? "默认助理" : `人设 ${index + 1}`)).trim() || (index === 0 ? "默认助理" : `人设 ${index + 1}`);
        const id = uniquePersonaId(source.id || (index === 0 ? "default" : name), index, usedIds);
        return {
            id,
            persona_name: name,
            persona_base_prompt: String(source.persona_base_prompt || source.base_prompt || ""),
            persona_ref_image: normalizePersonaImages(source.persona_ref_image || source.persona_ref_images || source.ref_images)
        };
    });

    if (!profiles.length) {
        profiles.push({ id: "default", persona_name: "默认助理", persona_base_prompt: "", persona_ref_image: [] });
    }

    const requestedActive = String(rawPersonaConfig.active_persona_id || "").trim();
    const requestedActiveLower = requestedActive.toLowerCase();
    const activeProfile = profiles.find((profile) => profile.id === requestedActive || profile.id.toLowerCase() === requestedActiveLower) || profiles[0];
    return {
        active_persona_id: activeProfile.id,
        profiles,
        persona_name: activeProfile.persona_name,
        persona_base_prompt: activeProfile.persona_base_prompt,
        persona_ref_image: activeProfile.persona_ref_image
    };
}

function getActivePersona() {
    if (!Array.isArray(state.persona_config.profiles) || !state.persona_config.profiles.length) {
        state.persona_config.profiles = [{ id: "default", persona_name: "默认助理", persona_base_prompt: "", persona_ref_image: [] }];
    }
    let active = state.persona_config.profiles.find((profile) => profile.id === state.persona_config.active_persona_id);
    if (!active) {
        active = state.persona_config.profiles[0];
        state.persona_config.active_persona_id = active.id;
    }
    active.persona_ref_image = normalizePersonaImages(active.persona_ref_image);
    return active;
}

function syncActivePersonaMirror() {
    const active = getActivePersona();
    state.persona_config.active_persona_id = active.id;
    state.persona_config.persona_name = active.persona_name;
    state.persona_config.persona_base_prompt = active.persona_base_prompt;
    state.persona_config.persona_ref_image = active.persona_ref_image;
}

function writeActivePersonaFieldsFromForm() {
    const active = getActivePersona();
    const nameInput = byId("persona_name");
    const promptInput = byId("persona_prompt");
    if (nameInput) active.persona_name = nameInput.value.trim() || "未命名人设";
    if (promptInput) active.persona_base_prompt = promptInput.value;
    syncActivePersonaMirror();
}

function bindPersonaFields() {
    syncActivePersonaMirror();
    const active = getActivePersona();
    byId("persona_name").value = active.persona_name || "默认助理";
    byId("persona_prompt").value = active.persona_base_prompt || "";
}

function showToast(message, type = "success") {
    const container = byId("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = type === "success" ? "✓" : "!";
    const text = document.createElement("span");
    text.textContent = message;
    toast.append(icon, text);
    container.appendChild(toast);
    setTimeout(() => toast.classList.add("toast-fadeout"), 2600);
    setTimeout(() => toast.remove(), 2920);
}

function setDirty(force) {
    if (!initialized) return;
    const isDirty = typeof force === "boolean" ? force : JSON.stringify(buildPayload()) !== savedSnapshot;
    document.body.classList.toggle("is-dirty", isDirty);
    const saveState = byId("save-state");
    if (saveState) saveState.textContent = isDirty ? "有未保存更改" : "配置已同步";
}

function updateMetrics() {
    byId("metric-image-nodes").textContent = state.providers.length;
    byId("metric-video-nodes").textContent = state.video_providers.length;
    byId("metric-presets").textContent = state.presets.filter((preset) => preset.name.trim()).length;
    byId("metric-today-images").textContent = state.usage_stats.total || 0;
}

function usageAccessLabel(level) {
    return ({
        limited: "受限",
        unlimited_user: "用户白名单",
        unlimited_group: "群白名单",
        blocked_user: "黑名单"
    }[level] || "受限");
}

function renderUsageStats() {
    const stats = state.usage_stats || {};
    byId("usage-stat-date").textContent = stats.date || "今日";
    byId("usage-stat-total").textContent = stats.total || 0;
    byId("usage-stat-users").textContent = (stats.users || []).length;
    byId("usage-stat-limit").textContent = state.usage_config.enable_daily_limit
        ? `${state.usage_config.daily_image_limit} 张/人${state.usage_config.enable_checkin ? " + 签到" : ""}`
        : "不限";

    const container = byId("usage-users-container");
    if (!container) return;
    const users = stats.users || [];
    container.innerHTML = users.map((user) => {
        const label = user.display_name && user.display_name !== user.user_id
            ? `${escapeHtml(user.display_name)} · ${escapeHtml(user.user_id)}`
            : escapeHtml(user.user_id);
        const accessLabel = usageAccessLabel(user.access_level);
        const isUnlimited = user.access_level === "unlimited_user" || user.access_level === "unlimited_group";
        const effectiveLimit = (state.usage_config.daily_image_limit || 0) + (user.bonus || 0);
        const quotaText = state.usage_config.enable_daily_limit
            ? (isUnlimited ? `${user.count} · 不限` : `${user.count}/${effectiveLimit}`)
            : `${user.count}`;
        const meta = [`最后生成 ${formatUsageTime(user.last_at)}`, accessLabel];
        if (user.group_id) meta.push(`群 ${escapeHtml(user.group_id)}`);
        if (user.bonus) meta.push(`签到 +${user.bonus}`);
        if (user.checkin_at) meta.push(`签到 ${formatUsageTime(user.checkin_at)}`);
        return `
            <div class="usage-user-row">
                <div class="usage-user-main">
                    <strong>${label}</strong>
                    <small>${meta.join(" · ")}</small>
                </div>
                <span>${quotaText}</span>
            </div>
        `;
    }).join("") || '<div class="empty-state">今日暂无生图记录</div>';
    updateMetrics();
}

async function loadUsageStats(showToastOnSuccess = false) {
    try {
        const res = await bridge.apiGet("get_usage_stats");
        state.usage_stats = normalizeUsageStats(res?.stats || res || {});
        renderUsageStats();
        if (showToastOnSuccess) showToast("统计已刷新");
    } catch (error) {
        console.error(error);
        if (showToastOnSuccess) showToast("统计刷新失败", "error");
        renderUsageStats();
    }
}

function renderSelectors() {
    const renderTo = (containerId, sourceList, inputId) => {
        const container = byId(containerId);
        const hiddenInput = byId(inputId);
        if (!container || !hiddenInput) return;
        const currentVal = hiddenInput.value;
        const html = sourceList.map((node) => {
            const nodeId = node.id || node["节点ID"];
            if (!nodeId) return "";
            const isActive = nodeId === currentVal;
            return `<button type="button" class="selector-chip ${isActive ? "active" : ""}" data-id="${escapeHtml(nodeId)}" data-input="${escapeHtml(inputId)}">${escapeHtml(nodeId)}</button>`;
        }).join("");
        container.innerHTML = html || '<span class="empty-hint">暂无可选节点</span>';
    };

    renderTo("sel-route-img", state.providers, "route_img");
    renderTo("sel-route-selfie", state.providers, "route_selfie");
    renderTo("sel-opt-chain", state.providers, "opt_chain");
    renderTo("sel-route-video", state.video_providers, "route_video");
}

function renderPersonaProfiles() {
    const container = byId("persona-profiles-container");
    if (!container) return;
    syncActivePersonaMirror();
    const profiles = state.persona_config.profiles || [];
    container.innerHTML = profiles.map((profile, index) => {
        const isActive = profile.id === state.persona_config.active_persona_id;
        const deleteControl = profiles.length > 1
            ? `<span class="persona-profile-delete" data-action="del-persona" data-index="${index}" title="删除人设">×</span>`
            : "";
        return `
            <button type="button" class="persona-profile-chip ${isActive ? "active" : ""}" data-action="switch-persona" data-index="${index}">
                <span class="persona-profile-name">${escapeHtml(profile.persona_name || "未命名人设")}</span>
                <small>${escapeHtml(profile.id)} · ${normalizePersonaImages(profile.persona_ref_image).length} 图</small>
                ${deleteControl}
            </button>
        `;
    }).join("");
}

function renderPersonaImages() {
    const container = byId("persona-upload-container");
    if (!container) return;
    syncActivePersonaMirror();
    container.querySelectorAll(".image-preview-wrapper").forEach((el) => el.remove());
    const trigger = byId("persona-upload-trigger");
    const images = getActivePersona().persona_ref_image || [];
    images.forEach((url, idx) => {
        const wrapper = document.createElement("div");
        wrapper.className = "image-preview-wrapper";
        const img = document.createElement("img");
        img.src = String(url || "");
        img.className = "image-preview";
        img.alt = `Reference ${idx + 1}`;
        const button = document.createElement("button");
        button.className = "btn-del-img";
        button.dataset.action = "del-persona-img";
        button.dataset.index = String(idx);
        button.type = "button";
        button.textContent = "×";
        wrapper.append(img, button);
        container.insertBefore(wrapper, trigger);
    });
}

function renderPresets() {
    const html = state.presets.map((p, i) => `
        <div class="list-item">
            <input type="text" class="input-glass preset-name" placeholder="快捷指令名" value="${escapeHtml(p.name)}" data-sync="preset-name" data-index="${i}">
            <span class="preset-arrow">→</span>
            <input type="text" class="input-glass preset-prompt" placeholder="底层提示词与参数" value="${escapeHtml(p.prompt)}" data-sync="preset-prompt" data-index="${i}">
            <button data-action="del-preset" data-index="${i}" class="btn-glass-secondary btn-danger" type="button">移除</button>
        </div>
    `).join("");
    byId("presets-container").innerHTML = html || '<div class="empty-state">尚未配置快捷指令</div>';
    updateMetrics();
}

function renderProviders() {
    const html = state.providers.map((p, i) => renderProviderCard(p, i, false)).join("");
    byId("providers-container").innerHTML = html || '<div class="empty-state">尚未配置图像节点</div>';
    updateMetrics();
}

function renderVideoProviders() {
    const html = state.video_providers.map((p, i) => renderProviderCard(p, i, true)).join("");
    byId("video-providers-container").innerHTML = html || '<div class="empty-state">尚未配置视频节点</div>';
    updateMetrics();
}

function renderProviderCard(p, i, isVideo) {
    const prefix = isVideo ? "vid" : "prov";
    const delAction = isVideo ? "del-video-provider" : "del-provider";
    const addModelAction = isVideo ? "add-vid-model" : "add-prov-model";
    const delModelAction = isVideo ? "del-vid-model" : "del-prov-model";
    const modelInputId = isVideo ? `new-model-vid-${i}` : `new-model-img-${i}`;
    const modes = isVideo
        ? [
            ["async_task", "异步轮询"],
            ["openai_sync", "同步阻塞"],
            ["openai_chat", "对话伪装"]
        ]
        : [
            ["openai_image", "标准生图"],
            ["openai_chat", "对话透传"]
        ];

    const modeChips = modes.map(([value, label]) => {
        const active = isVideo ? (p.api_type || "").includes(value) : p.api_type === value;
        return `<button type="button" class="api-chip ${active ? "active" : ""}" data-sync="${prefix}-api" data-index="${i}" data-val="${value}">${label}</button>`;
    }).join("");

    const modelChips = (p.available_models || []).map((model, modelIdx) => `
        <button type="button" class="api-chip ${p.model === model ? "active" : ""}" data-sync="${prefix}-model-select" data-index="${i}" data-val="${escapeHtml(model)}">
            <span>${escapeHtml(model)}</span>
            <span class="chip-del" data-action="${delModelAction}" data-index="${i}" data-midx="${modelIdx}">×</span>
        </button>
    `).join("") || '<span class="empty-hint">暂无模型</span>';

    return `
        <div class="node-card">
            <div class="node-card-header">
                <input type="text" class="input-glass node-id-input" placeholder="${isVideo ? "视频节点 ID" : "图像节点 ID"}" value="${escapeHtml(p.id)}" data-sync="${prefix}-id" data-index="${i}">
                <button data-action="${delAction}" data-index="${i}" class="btn-ghost btn-danger" type="button">移除节点</button>
            </div>
            <div class="node-form-grid">
                <div class="form-group">
                    <label>${isVideo ? "调用协议" : "接口模式"}</label>
                    <div class="chip-group">${modeChips}</div>
                </div>
                <div class="form-group">
                    <label>接口地址</label>
                    <input type="text" class="input-glass" value="${escapeHtml(p.base_url)}" data-sync="${prefix}-url" data-index="${i}">
                </div>
                <div class="form-group full-width">
                    <label>${isVideo ? "视频模型池" : "算力模型池"}</label>
                    <div class="chip-group">${modelChips}</div>
                    <div class="model-row">
                        <input type="text" class="input-glass" id="${modelInputId}" data-model-input="${isVideo ? "video" : "image"}" data-index="${i}" placeholder="${isVideo ? "输入视频模型名称" : "输入新模型名称"}">
                        <button data-action="${addModelAction}" data-index="${i}" class="btn-glass-secondary" type="button">添加模型</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>请求超时</label>
                    <input type="number" class="input-glass" value="${escapeHtml(p.timeout)}" min="1" data-sync="${prefix}-time" data-index="${i}">
                </div>
                <div class="form-group full-width">
                    <label>API Keys</label>
                    <textarea class="input-glass" rows="3" data-sync="${prefix}-keys" data-index="${i}">${escapeHtml(p.api_keys)}</textarea>
                </div>
            </div>
        </div>
    `;
}

function bindBasicFields() {
    byId("perm_allowed_users").value = state.permission_config.allowed_users || "";
    byId("perm_blocked_users").value = state.permission_config.blocked_users || "";
    byId("perm_unlimited_groups").value = state.permission_config.unlimited_groups || "";
    byId("usage_enable").checked = Boolean(state.usage_config.enable_daily_limit);
    byId("usage_daily_limit").value = state.usage_config.daily_image_limit || 20;
    byId("usage_checkin_enable").checked = Boolean(state.usage_config.enable_checkin);
    byId("usage_checkin_min").value = state.usage_config.checkin_bonus_min ?? 1;
    byId("usage_checkin_max").value = state.usage_config.checkin_bonus_max ?? 3;
    byId("route_img").value = state.router_config.chain_text2img || "node_1";
    byId("route_selfie").value = state.router_config.chain_selfie || "node_1";
    byId("route_video").value = state.router_config.chain_video || "video_node_1";
    bindPersonaFields();
    byId("opt_enable").checked = Boolean(state.optimizer_config.enable_optimizer);
    byId("opt_style").value = state.optimizer_config.optimizer_style || "手机日常原生感";
    byId("opt_chain").value = state.optimizer_config.chain_optimizer || "node_1";
    byId("opt_model").value = state.optimizer_config.optimizer_model || "gpt-4o-mini";
    byId("opt_timeout").value = state.optimizer_config.optimizer_timeout || 15;
    byId("opt_batch").value = state.optimizer_config.max_batch_count || 0;
    byId("opt_custom").value = state.optimizer_config.optimizer_custom_prompt || "";
    byId("verbose_report").checked = Boolean(state.verbose_report);
}

function readBasicFields() {
    state.permission_config.allowed_users = normalizeIdText(byId("perm_allowed_users").value);
    state.permission_config.blocked_users = normalizeIdText(byId("perm_blocked_users").value);
    state.permission_config.unlimited_groups = normalizeIdText(byId("perm_unlimited_groups").value);
    state.usage_config.enable_daily_limit = byId("usage_enable").checked;
    state.usage_config.daily_image_limit = Math.max(1, parseInt(byId("usage_daily_limit").value, 10) || 20);
    state.usage_config.enable_checkin = byId("usage_checkin_enable").checked;
    state.usage_config.checkin_bonus_min = readNonnegativeIntInput("usage_checkin_min", 0);
    state.usage_config.checkin_bonus_max = readNonnegativeIntInput("usage_checkin_max", 0);
    if (state.usage_config.checkin_bonus_max < state.usage_config.checkin_bonus_min) {
        state.usage_config.checkin_bonus_max = state.usage_config.checkin_bonus_min;
    }
    state.router_config.chain_text2img = byId("route_img").value.trim();
    state.router_config.chain_selfie = byId("route_selfie").value.trim();
    state.router_config.chain_video = byId("route_video").value.trim();
    writeActivePersonaFieldsFromForm();
    state.optimizer_config.enable_optimizer = byId("opt_enable").checked;
    state.optimizer_config.optimizer_style = byId("opt_style").value;
    state.optimizer_config.chain_optimizer = byId("opt_chain").value.trim();
    state.optimizer_config.optimizer_model = byId("opt_model").value.trim();
    state.optimizer_config.optimizer_timeout = parseFloat(byId("opt_timeout").value) || 15;
    state.optimizer_config.max_batch_count = parseInt(byId("opt_batch").value, 10) || 0;
    state.optimizer_config.optimizer_custom_prompt = byId("opt_custom").value;
    state.verbose_report = byId("verbose_report").checked;
}

function buildPayload() {
    readBasicFields();
    return {
        permission_config: state.permission_config,
        usage_config: state.usage_config,
        persona_config: state.persona_config,
        optimizer_config: state.optimizer_config,
        router_config: state.router_config,
        presets: state.presets.filter((p) => p.name.trim()).map((p) => `${p.name.trim()}:${p.prompt || ""}`),
        providers: state.providers,
        video_providers: state.video_providers,
        verbose_report: state.verbose_report
    };
}

function validateConfig() {
    const checkinMin = readNonnegativeIntInput("usage_checkin_min", 0);
    const checkinMax = readNonnegativeIntInput("usage_checkin_max", 0);
    if (byId("usage_checkin_enable").checked && checkinMax < checkinMin) return "签到奖励最大张数不能小于最小张数";

    readBasicFields();
    const validateList = (list, label) => {
        const ids = list.map((node) => String(node.id || "").trim()).filter(Boolean);
        const duplicates = ids.filter((id, idx) => ids.indexOf(id) !== idx);
        if (list.some((node) => !String(node.id || "").trim())) return `${label}存在未填写节点 ID`;
        if (duplicates.length) return `${label}节点 ID 重复：${duplicates[0]}`;
        return "";
    };
    writeActivePersonaFieldsFromForm();
    const personaIds = state.persona_config.profiles.map((profile) => String(profile.id || "").trim()).filter(Boolean);
    const personaNames = state.persona_config.profiles.map((profile) => String(profile.persona_name || "").trim());
    const duplicatePersonaIds = personaIds.filter((id, idx) => personaIds.indexOf(id) !== idx);
    if (!state.persona_config.profiles.length) return "至少需要保留一个人设";
    if (personaNames.some((name) => !name)) return "人设名称不能为空";
    if (duplicatePersonaIds.length) return `人设 ID 重复：${duplicatePersonaIds[0]}`;
    if (state.usage_config.enable_daily_limit && state.usage_config.daily_image_limit <= 0) return "每日生图上限必须大于 0";
    return validateList(state.providers, "图像") || validateList(state.video_providers, "视频");
}

function setActiveTab(navItem) {
    const targetId = navItem.getAttribute("data-target");
    const targetPane = byId(targetId);
    if (!targetPane) return;
    const content = document.querySelector(".content");
    content?.classList.add("is-switching");
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === navItem));
    document.querySelectorAll(".tab-pane").forEach((pane) => pane.classList.toggle("active", pane === targetPane));
    byId("active-title").textContent = targetPane.dataset.title || navItem.textContent.trim();
    navItem.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    window.setTimeout(() => content?.classList.remove("is-switching"), 260);
}

function animateAdd(containerId) {
    setTimeout(() => {
        const container = byId(containerId);
        const el = container?.lastElementChild;
        if (!el) return;
        el.classList.add("node-enter");
        el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 10);
}

function animateDel(containerId, stateArray, index, renderFn, callback) {
    const container = byId(containerId);
    const el = container?.children[index];
    if (!el) {
        stateArray.splice(index, 1);
        renderFn();
        callback?.();
        setDirty();
        return;
    }
    el.classList.add("node-exit");
    setTimeout(() => {
        stateArray.splice(index, 1);
        renderFn();
        callback?.();
        setDirty();
    }, 220);
}

function addModel(kind, idx) {
    const isVideo = kind === "video";
    const list = isVideo ? state.video_providers : state.providers;
    const input = byId(isVideo ? `new-model-vid-${idx}` : `new-model-img-${idx}`);
    const newModel = input?.value.trim();
    if (!newModel) return;
    if (list[idx].available_models.includes(newModel)) {
        showToast("模型已存在", "error");
        return;
    }
    list[idx].available_models.push(newModel);
    if (!list[idx].model) list[idx].model = newModel;
    input.value = "";
    isVideo ? renderVideoProviders() : renderProviders();
    setDirty();
}

function switchPersona(index) {
    const profiles = state.persona_config.profiles || [];
    if (!profiles[index]) return;
    writeActivePersonaFieldsFromForm();
    state.persona_config.active_persona_id = profiles[index].id;
    bindPersonaFields();
    renderPersonaProfiles();
    renderPersonaImages();
    showToast(`已切换至「${profiles[index].persona_name || "未命名人设"}」`);
    setDirty();
}

function addPersona() {
    writeActivePersonaFieldsFromForm();
    const usedIds = new Set((state.persona_config.profiles || []).map((profile) => profile.id));
    const index = state.persona_config.profiles.length;
    const id = uniquePersonaId(`persona_${index + 1}`, index, usedIds);
    const profile = {
        id,
        persona_name: `人设 ${index + 1}`,
        persona_base_prompt: "",
        persona_ref_image: []
    };
    state.persona_config.profiles.push(profile);
    state.persona_config.active_persona_id = profile.id;
    bindPersonaFields();
    renderPersonaProfiles();
    renderPersonaImages();
    showToast("已新增人设");
    setDirty();
    byId("persona_name")?.focus();
}

function deletePersona(index) {
    const profiles = state.persona_config.profiles || [];
    if (profiles.length <= 1 || !profiles[index]) {
        showToast("至少需要保留一个人设", "error");
        return;
    }
    const removingActive = profiles[index].id === state.persona_config.active_persona_id;
    const removed = profiles.splice(index, 1)[0];
    if (removingActive) {
        const fallback = profiles[Math.max(0, index - 1)] || profiles[0];
        state.persona_config.active_persona_id = fallback.id;
        bindPersonaFields();
        renderPersonaImages();
    }
    renderPersonaProfiles();
    showToast(`已删除「${removed.persona_name || removed.id}」`);
    setDirty();
}

function setupEventDelegation() {
    const fileInput = byId("hidden-file-input");
    const pressableSelector = ".nav-item, .btn-primary, .btn-secondary, .btn-glass-secondary, .btn-ghost, .upload-trigger, .selector-chip, .api-chip, .persona-profile-chip";

    document.body.addEventListener("pointerdown", (e) => {
        const target = e.target.closest(pressableSelector);
        if (!target || target.disabled) return;
        target.classList.add("is-pressing");
    });

    const clearPressed = () => {
        document.querySelectorAll(".is-pressing").forEach((item) => item.classList.remove("is-pressing"));
    };
    document.addEventListener("pointerup", clearPressed);
    document.addEventListener("pointercancel", clearPressed);
    document.addEventListener("pointerleave", clearPressed);
    document.addEventListener("click", clearPressed);

    document.body.addEventListener("click", (e) => {
        const navItem = e.target.closest(".nav-item");
        if (navItem) {
            setActiveTab(navItem);
            return;
        }

        const chip = e.target.closest(".selector-chip");
        if (chip) {
            const inputId = chip.getAttribute("data-input");
            byId(inputId).value = chip.getAttribute("data-id");
            document.querySelectorAll(`.selector-chip[data-input="${inputId}"]`).forEach((item) => item.classList.remove("active"));
            chip.classList.add("active");
            setDirty();
            return;
        }

        const apiChip = e.target.closest(".api-chip");
        if (apiChip && !e.target.closest(".chip-del")) {
            const sync = apiChip.getAttribute("data-sync");
            const idx = parseInt(apiChip.getAttribute("data-index"), 10);
            const val = apiChip.getAttribute("data-val");
            if (sync === "prov-api") state.providers[idx].api_type = val;
            if (sync === "vid-api") state.video_providers[idx].api_type = val;
            if (sync === "prov-model-select") state.providers[idx].model = val;
            if (sync === "vid-model-select") state.video_providers[idx].model = val;
            sync.startsWith("vid") ? renderVideoProviders() : renderProviders();
            setDirty();
            return;
        }

        if (e.target.closest("#persona-upload-trigger")) {
            fileInput.click();
            return;
        }

        const btn = e.target.closest("[data-action]");
        if (!btn) return;
        const act = btn.getAttribute("data-action");
        const idx = parseInt(btn.getAttribute("data-index"), 10);

        if (act === "save-config") saveConfig(btn);
        if (act === "refresh-usage") {
            loadUsageStats(true);
            return;
        }
        if (act === "switch-persona") {
            switchPersona(idx);
            return;
        }
        if (act === "add-persona") {
            addPersona();
            return;
        }
        if (act === "del-persona") {
            deletePersona(idx);
            return;
        }
        if (act === "add-preset") {
            state.presets.push({ name: "", prompt: "" });
            renderPresets();
            animateAdd("presets-container");
            setDirty();
        }
        if (act === "del-preset") animateDel("presets-container", state.presets, idx, renderPresets);
        if (act === "add-provider") {
            state.providers.push({ id: `node_${state.providers.length + 1}`, api_type: "openai_image", base_url: "", model: "", available_models: [], api_keys: "", timeout: 60 });
            renderProviders();
            renderSelectors();
            animateAdd("providers-container");
            setDirty();
        }
        if (act === "del-provider") animateDel("providers-container", state.providers, idx, renderProviders, renderSelectors);
        if (act === "add-video-provider") {
            state.video_providers.push({ id: `video_node_${state.video_providers.length + 1}`, api_type: "async_task", base_url: "", model: "", available_models: [], api_keys: "", timeout: 300 });
            renderVideoProviders();
            renderSelectors();
            animateAdd("video-providers-container");
            setDirty();
        }
        if (act === "del-video-provider") animateDel("video-providers-container", state.video_providers, idx, renderVideoProviders, renderSelectors);
        if (act === "del-persona-img") {
            animateDel("persona-upload-container", getActivePersona().persona_ref_image, idx, () => {
                syncActivePersonaMirror();
                renderPersonaImages();
                renderPersonaProfiles();
            });
        }
        if (act === "add-prov-model") addModel("image", idx);
        if (act === "add-vid-model") addModel("video", idx);
        if (act === "del-prov-model") {
            const modelIdx = parseInt(btn.getAttribute("data-midx"), 10);
            const removed = state.providers[idx].available_models.splice(modelIdx, 1)[0];
            if (state.providers[idx].model === removed) state.providers[idx].model = state.providers[idx].available_models[0] || "";
            renderProviders();
            setDirty();
        }
        if (act === "del-vid-model") {
            const modelIdx = parseInt(btn.getAttribute("data-midx"), 10);
            const removed = state.video_providers[idx].available_models.splice(modelIdx, 1)[0];
            if (state.video_providers[idx].model === removed) state.video_providers[idx].model = state.video_providers[idx].available_models[0] || "";
            renderVideoProviders();
            setDirty();
        }
    });

    document.body.addEventListener("input", (e) => {
        const input = e.target;
        if (!input.hasAttribute("data-sync")) {
            if (["INPUT", "TEXTAREA", "SELECT"].includes(input.tagName)) setDirty();
            return;
        }
        const s = input.getAttribute("data-sync");
        const i = parseInt(input.getAttribute("data-index"), 10);
        const v = input.value;
        if (s === "preset-name") state.presets[i].name = v;
        if (s === "preset-prompt") state.presets[i].prompt = v;
        if (s === "persona-name") {
            getActivePersona().persona_name = v.trim() || "未命名人设";
            syncActivePersonaMirror();
            renderPersonaProfiles();
        }
        if (s === "persona-prompt") {
            getActivePersona().persona_base_prompt = v;
            syncActivePersonaMirror();
        }
        if (s === "prov-id") state.providers[i].id = v;
        if (s === "prov-url") state.providers[i].base_url = v;
        if (s === "prov-time") state.providers[i].timeout = parseFloat(v) || 60;
        if (s === "prov-keys") state.providers[i].api_keys = v;
        if (s === "vid-id") state.video_providers[i].id = v;
        if (s === "vid-url") state.video_providers[i].base_url = v;
        if (s === "vid-time") state.video_providers[i].timeout = parseFloat(v) || 300;
        if (s === "vid-keys") state.video_providers[i].api_keys = v;
        setDirty();
    });

    document.body.addEventListener("change", (e) => {
        const input = e.target;
        if (input.hasAttribute("data-sync") && ["prov-id", "vid-id"].includes(input.getAttribute("data-sync"))) {
            renderSelectors();
        }
        setDirty();
    });

    document.body.addEventListener("keydown", (e) => {
        const input = e.target.closest("[data-model-input]");
        if (!input || e.key !== "Enter") return;
        e.preventDefault();
        addModel(input.getAttribute("data-model-input"), parseInt(input.getAttribute("data-index"), 10));
    });

    fileInput.addEventListener("change", (e) => {
        const files = Array.from(e.target.files || []);
        if (!files.length) return;
        let loadedCount = 0;
        const activePersona = getActivePersona();
        activePersona.persona_ref_image ||= [];
        files.forEach((file) => {
            const reader = new FileReader();
            reader.onload = (evt) => {
                activePersona.persona_ref_image.push(evt.target.result);
                loadedCount += 1;
                if (loadedCount === files.length) {
                    syncActivePersonaMirror();
                    renderPersonaImages();
                    renderPersonaProfiles();
                    showToast(`已添加 ${files.length} 张图片`);
                    setDirty();
                }
            };
            reader.readAsDataURL(file);
        });
        fileInput.value = "";
    });
}

async function saveConfig(btn) {
    const validationError = validateConfig();
    if (validationError) {
        showToast(validationError, "error");
        return;
    }
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "保存中...";
    try {
        const payload = buildPayload();
        const res = await bridge.apiPost("save_config", payload);
        if (res?.success) {
            savedSnapshot = JSON.stringify(payload);
            setDirty(false);
            renderUsageStats();
            showToast("配置已保存");
        } else {
            showToast(res?.message || "保存失败", "error");
        }
    } catch (error) {
        console.error(error);
        showToast("网络错误", "error");
    } finally {
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = originalText;
        }, 420);
    }
}

async function init() {
    await bridge.ready();
    const rawConfig = await bridge.apiGet("get_config") || {};
    const perm = rawConfig.permission_config || rawConfig;
    const pers = rawConfig.persona_config || rawConfig;
    const opt = rawConfig.optimizer_config || rawConfig;
    const route = rawConfig.router_config || rawConfig;

    state.permission_config = normalizePermissionConfig(perm);
    state.usage_config = normalizeUsageConfig(rawConfig.usage_config || {});
    state.router_config.chain_text2img = deepFind(route, ["chain_text2img"], "node_1");
    state.router_config.chain_selfie = deepFind(route, ["chain_selfie"], "node_1");
    state.router_config.chain_video = deepFind(route, ["chain_video"], "video_node_1");
    state.persona_config = normalizePersonaProfiles(pers);

    state.optimizer_config.enable_optimizer = deepFind(opt, ["enable_optimizer"], true);
    state.optimizer_config.optimizer_style = deepFind(opt, ["optimizer_style"], "手机日常原生感");
    state.optimizer_config.chain_optimizer = deepFind(opt, ["chain_optimizer"], "node_1");
    state.optimizer_config.optimizer_model = deepFind(opt, ["optimizer_model"], "gpt-4o-mini");
    state.optimizer_config.optimizer_timeout = parseFloat(deepFind(opt, ["optimizer_timeout"], 15)) || 15;
    state.optimizer_config.max_batch_count = parseInt(deepFind(opt, ["max_batch_count"], 0), 10) || 0;
    state.optimizer_config.optimizer_custom_prompt = deepFind(opt, ["optimizer_custom_prompt"]);

    state.presets = (rawConfig.presets || []).map(parsePreset);
    state.providers = (rawConfig.providers || []).map((p) => {
        const availableModels = normalizeModelList(p.available_models?.length ? p.available_models : (p.model || p["模型名称"] || ""));
        const model = p.model && !String(p.model).includes(",") ? p.model : (availableModels[0] || "");
        return {
            id: p.id || p["节点ID"] || "",
            api_type: p.api_type || p["接口模式"] || "openai_image",
            base_url: p.base_url || p["接口地址 (需含/v1)"] || "",
            model,
            available_models: availableModels,
            timeout: p.timeout || p["超时时间(秒)"] || 60,
            api_keys: normalizeTextAreaKeys(p.api_keys || p["API密钥"] || "")
        };
    });

    state.video_providers = (rawConfig.video_providers || []).map((p) => {
        const availableModels = normalizeModelList(p.available_models?.length ? p.available_models : (p.model || p["模型名称"] || ""));
        const model = p.model && !String(p.model).includes(",") ? p.model : (availableModels[0] || "");
        return {
            id: p.id || p["节点ID"] || "",
            api_type: p.api_type || p["接口模式"] || "async_task",
            base_url: p.base_url || p["接口地址 (需含/v1或/v2)"] || p["接口地址 (需含/v1)"] || "",
            model,
            available_models: availableModels,
            timeout: p.timeout || p["超时时间(秒)"] || 300,
            api_keys: normalizeTextAreaKeys(p.api_keys || p["API密钥"] || "")
        };
    });

    state.verbose_report = Boolean(rawConfig.verbose_report);

    bindBasicFields();
    renderSelectors();
    renderPersonaProfiles();
    renderPresets();
    renderProviders();
    renderVideoProviders();
    renderPersonaImages();
    renderUsageStats();
    setupEventDelegation();
    await loadUsageStats(false);
    updateMetrics();
    initialized = true;
    savedSnapshot = JSON.stringify(buildPayload());
    setDirty(false);
}

init().catch((error) => {
    console.error(error);
    showToast("配置页初始化失败", "error");
});
