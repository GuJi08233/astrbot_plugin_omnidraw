const bridge = window.AstrBotPluginPage;

let state = {
    permission_config: {}, persona_config: { persona_ref_image: [] }, optimizer_config: {}, router_config: {},
    presets: [], providers: [], video_providers: [], verbose_report: false
};

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span class="toast-icon">${type === 'success' ? '✓' : '✕'}</span> <span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => toast.classList.add('toast-fadeout'), 2500);
    setTimeout(() => toast.remove(), 2800);
}

function renderSelectors() {
    const renderTo = (containerId, sourceList, inputId) => {
        const container = document.getElementById(containerId);
        const hiddenInput = document.getElementById(inputId);
        if(!container || !hiddenInput) return;
        const currentVal = hiddenInput.value;
        container.innerHTML = sourceList.map(node => {
            const nodeId = node.id || node['节点ID'];
            if(!nodeId) return '';
            const isActive = nodeId === currentVal;
            return `<div class="selector-chip ${isActive ? 'active' : ''}" data-id="${nodeId}" data-input="${inputId}">${nodeId}</div>`;
        }).join('') || '<span class="empty-hint">请先在「算力集群」中配置并填写节点 ID</span>';
    };
    renderTo('sel-route-img', state.providers, 'route_img');
    renderTo('sel-route-selfie', state.providers, 'route_selfie');
    renderTo('sel-opt-chain', state.providers, 'opt_chain');
    renderTo('sel-route-video', state.video_providers, 'route_video');
}

function renderPersonaImages() {
    const container = document.getElementById('persona-upload-container');
    if(!container) return;
    container.querySelectorAll('.image-preview-wrapper').forEach(el => el.remove());
    const trigger = document.getElementById('persona-upload-trigger');
    const images = state.persona_config.persona_ref_image || [];
    images.forEach((url, idx) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'image-preview-wrapper';
        wrapper.innerHTML = `<img src="${url}" class="image-preview" alt="Ref" /><button class="btn-del-img" data-action="del-persona-img" data-index="${idx}">×</button>`;
        container.insertBefore(wrapper, trigger);
    });
}

const deepFind = (obj, keys, def = "") => {
    if (!obj) return def;
    for (const key of keys) { if (obj[key] !== undefined) return obj[key]; }
    return def;
};

async function init() {
    const context = await bridge.ready();
    const rawConfig = await bridge.apiGet("get_config") || {};
    
    const perm = rawConfig.permission_config || rawConfig;
    const pers = rawConfig.persona_config || rawConfig;
    const opt = rawConfig.optimizer_config || rawConfig;
    const route = rawConfig.router_config || rawConfig;

    state.permission_config.allowed_users = pers.allowed_users || perm.allowed_users || "";
    state.router_config.chain_text2img = deepFind(route, ["chain_text2img"], "node_1");
    state.router_config.chain_selfie = deepFind(route, ["chain_selfie"], "node_1");
    state.router_config.chain_video = deepFind(route, ["chain_video"], "video_node_1");
    state.persona_config.persona_name = deepFind(pers, ["persona_name"], "默认助理");
    state.persona_config.persona_base_prompt = deepFind(pers, ["persona_base_prompt"]);
    
    let rawImage = deepFind(pers, ["persona_ref_image"]);
    if (typeof rawImage === 'string' && rawImage.trim() !== '') state.persona_config.persona_ref_image = [rawImage];
    else if (Array.isArray(rawImage)) state.persona_config.persona_ref_image = rawImage;
    else state.persona_config.persona_ref_image = [];

    state.optimizer_config.enable_optimizer = deepFind(opt, ["enable_optimizer"], true);
    state.optimizer_config.optimizer_style = deepFind(opt, ["optimizer_style"], "手机日常原生感");
    state.optimizer_config.chain_optimizer = deepFind(opt, ["chain_optimizer"], "node_1");
    state.optimizer_config.optimizer_model = deepFind(opt, ["optimizer_model"], "gpt-4o-mini");
    state.optimizer_config.optimizer_timeout = parseFloat(deepFind(opt, ["optimizer_timeout"], 15));
    state.optimizer_config.max_batch_count = parseInt(deepFind(opt, ["max_batch_count"], 0));
    state.optimizer_config.optimizer_custom_prompt = deepFind(opt, ["optimizer_custom_prompt"]);

    state.presets = (rawConfig.presets || []).map(p => typeof p === 'string' ? { name: p.split(':')[0], prompt: p.split(':')[1] } : p);
    
    state.providers = (rawConfig.providers || []).map(p => {
        let avail = p.available_models || [];
        if(avail.length === 0) {
            const mRaw = p.model || p['模型名称'] || '';
            avail = mRaw.split(',').map(s=>s.trim()).filter(Boolean);
        }
        let defModel = p.model || (avail.length > 0 ? avail[0] : "");
        return {
            id: p.id || p['节点ID'] || '', api_type: p.api_type || p['接口模式'] || 'openai_image',
            base_url: p.base_url || p['接口地址 (需含/v1)'] || '', model: defModel, available_models: avail,
            timeout: p.timeout || p['超时时间(秒)'] || 60, api_keys: Array.isArray(p.api_keys) ? p.api_keys.join('\n') : (p.api_keys || p['API密钥'] || '')
        };
    });
    
    state.video_providers = (rawConfig.video_providers || []).map(p => {
        let avail = p.available_models || [];
        if(avail.length === 0) {
            const mRaw = p.model || p['模型名称'] || '';
            avail = mRaw.split(',').map(s=>s.trim()).filter(Boolean);
        }
        let defModel = p.model || (avail.length > 0 ? avail[0] : "");
        return {
            id: p.id || p['节点ID'] || '', api_type: p.api_type || p['接口模式'] || 'async_task',
            base_url: p.base_url || p['接口地址 (需含/v1或/v2)'] || p['接口地址 (需含/v1)'] || '',
            model: defModel, available_models: avail, timeout: p.timeout || p['超时时间(秒)'] || 300,
            api_keys: Array.isArray(p.api_keys) ? p.api_keys.join('\n') : (p.api_keys || p['API密钥'] || '')
        };
    });

    state.verbose_report = rawConfig.verbose_report || false;

    bindBasicFields();
    renderSelectors();
    renderPresets();
    renderProviders();
    renderVideoProviders();
    setupEventDelegation();
    renderPersonaImages();
}

function bindBasicFields() {
    document.getElementById("perm_allowed_users").value = state.permission_config.allowed_users;
    document.getElementById("route_img").value = state.router_config.chain_text2img;
    document.getElementById("route_selfie").value = state.router_config.chain_selfie;
    document.getElementById("route_video").value = state.router_config.chain_video;
    document.getElementById("persona_name").value = state.persona_config.persona_name;
    document.getElementById("persona_prompt").value = state.persona_config.persona_base_prompt;
    document.getElementById("opt_enable").checked = state.optimizer_config.enable_optimizer;
    document.getElementById("opt_style").value = state.optimizer_config.optimizer_style;
    document.getElementById("opt_chain").value = state.optimizer_config.chain_optimizer;
    document.getElementById("opt_model").value = state.optimizer_config.optimizer_model;
    document.getElementById("opt_timeout").value = state.optimizer_config.optimizer_timeout;
    document.getElementById("opt_batch").value = state.optimizer_config.max_batch_count;
    document.getElementById("opt_custom").value = state.optimizer_config.optimizer_custom_prompt;
    document.getElementById("verbose_report").checked = state.verbose_report;
}

function readBasicFields() {
    state.permission_config.allowed_users = document.getElementById("perm_allowed_users").value;
    state.router_config.chain_text2img = document.getElementById("route_img").value;
    state.router_config.chain_selfie = document.getElementById("route_selfie").value;
    state.router_config.chain_video = document.getElementById("route_video").value;
    state.persona_config.persona_name = document.getElementById("persona_name").value;
    state.persona_config.persona_base_prompt = document.getElementById("persona_prompt").value;
    state.optimizer_config.enable_optimizer = document.getElementById("opt_enable").checked;
    state.optimizer_config.optimizer_style = document.getElementById("opt_style").value;
    state.optimizer_config.chain_optimizer = document.getElementById("opt_chain").value;
    state.optimizer_config.optimizer_model = document.getElementById("opt_model").value;
    state.optimizer_config.optimizer_timeout = parseFloat(document.getElementById("opt_timeout").value);
    state.optimizer_config.max_batch_count = parseInt(document.getElementById("opt_batch").value);
    state.optimizer_config.optimizer_custom_prompt = document.getElementById("opt_custom").value;
    state.verbose_report = document.getElementById("verbose_report").checked;
}

function renderPresets() {
    const html = state.presets.map((p, i) => `
        <div class="list-item">
            <input type="text" class="input-glass preset-name" style="width: 140px; border:none; background:transparent;" placeholder="快捷指令名" value="${p.name}" data-sync="preset-name" data-index="${i}">
            <span style="color:var(--text-muted); font-weight: bold; margin: 0 10px;">→</span>
            <input type="text" class="input-glass preset-prompt" style="flex:1; border:none; background:transparent;" placeholder="底层的英文描述与参数" value="${p.prompt}" data-sync="preset-prompt" data-index="${i}">
            <button data-action="del-preset" data-index="${i}" class="btn-glass-secondary" style="border:none; color:var(--text-muted); font-size:16px;">×</button>
        </div>
    `).join('');
    document.getElementById("presets-container").innerHTML = html || '<div style="text-align:center; padding: 30px; color: var(--text-muted);">尚未配置快捷指令</div>';
}

function renderProviders() {
    const html = state.providers.map((p, i) => `
        <div class="glass-card" style="padding: 24px; margin-bottom: 16px;">
            <div class="card-header" style="margin-bottom: 16px; border-bottom: 1px solid rgba(0,0,0,0.05); padding-bottom: 12px;">
                <input type="text" class="input-glass" style="width: 200px; font-weight:bold; font-size: 16px; background: transparent; border:none; border-bottom: 1px solid transparent;" placeholder="输入节点 ID" value="${p.id}" data-sync="prov-id" data-index="${i}">
                <button data-action="del-provider" data-index="${i}" style="background:transparent; border:none; color:var(--danger); font-weight:bold; cursor:pointer;">移除</button>
            </div>
            <div class="grid-2-col">
                <div class="form-group"><label>接口模式</label>
                    <div class="chip-group">
                        <div class="api-chip ${p.api_type==='openai_image'?'active':''}" data-sync="prov-api" data-index="${i}" data-val="openai_image">标准生图</div>
                        <div class="api-chip ${p.api_type==='openai_chat'?'active':''}" data-sync="prov-api" data-index="${i}" data-val="openai_chat">对话透传</div>
                    </div>
                </div>
                <div class="form-group"><label>接口地址 (需含/v1)</label><input type="text" class="input-glass" value="${p.base_url}" data-sync="prov-url" data-index="${i}"></div>
                
                <div class="form-group full-width">
                    <label>算力模型池 (点击设为默认，点击 × 移除)</label>
                    <div class="chip-group" style="margin-bottom: 8px;">
                        ${(p.available_models || []).map((m, mIdx) => `
                            <div class="api-chip ${p.model === m ? 'active' : ''}" data-sync="prov-model-select" data-index="${i}" data-val="${m}">
                                ${m} <span class="chip-del" data-action="del-prov-model" data-index="${i}" data-midx="${mIdx}">×</span>
                            </div>
                        `).join('')}
                        ${(p.available_models || []).length === 0 ? '<span class="empty-hint">暂无模型，请在下方添加</span>' : ''}
                    </div>
                    <div style="display:flex; gap:10px;">
                        <input type="text" class="input-glass" id="new-model-img-${i}" placeholder="输入新模型名称 (如 dall-e-3)" style="flex:1;">
                        <button data-action="add-prov-model" data-index="${i}" class="btn-glass-secondary">添加模型</button>
                    </div>
                </div>

                <div class="form-group"><label>请求超时</label><input type="number" class="input-glass" value="${p.timeout}" data-sync="prov-time" data-index="${i}"></div>
                <div class="form-group full-width"><label>API Keys</label><textarea class="input-glass" rows="1" data-sync="prov-keys" data-index="${i}">${p.api_keys}</textarea></div>
            </div>
        </div>
    `).join('');
    document.getElementById("providers-container").innerHTML = html;
}

function renderVideoProviders() {
    const html = state.video_providers.map((p, i) => `
        <div class="glass-card" style="padding: 24px; margin-bottom: 16px;">
            <div class="card-header" style="margin-bottom: 16px; border-bottom: 1px solid rgba(0,0,0,0.05); padding-bottom: 12px;">
                <input type="text" class="input-glass" style="width: 200px; font-weight:bold; font-size: 16px; background: transparent; border:none; border-bottom: 1px solid transparent;" placeholder="输入视频节点 ID" value="${p.id}" data-sync="vid-id" data-index="${i}">
                <button data-action="del-video-provider" data-index="${i}" style="background:transparent; border:none; color:var(--danger); font-weight:bold; cursor:pointer;">移除</button>
            </div>
            <div class="grid-2-col">
                <div class="form-group"><label>调用协议</label>
                    <div class="chip-group">
                        <div class="api-chip ${(p.api_type||'').includes('async_task')?'active':''}" data-sync="vid-api" data-index="${i}" data-val="async_task">异步轮询</div>
                        <div class="api-chip ${(p.api_type||'').includes('openai_sync')?'active':''}" data-sync="vid-api" data-index="${i}" data-val="openai_sync">同步阻塞</div>
                        <div class="api-chip ${(p.api_type||'').includes('openai_chat')?'active':''}" data-sync="vid-api" data-index="${i}" data-val="openai_chat">对话伪装</div>
                    </div>
                </div>
                <div class="form-group"><label>接口地址</label><input type="text" class="input-glass" value="${p.base_url}" data-sync="vid-url" data-index="${i}"></div>
                
                <div class="form-group full-width">
                    <label>视频模型池 (点击设为默认，点击 × 移除)</label>
                    <div class="chip-group" style="margin-bottom: 8px;">
                        ${(p.available_models || []).map((m, mIdx) => `
                            <div class="api-chip ${p.model === m ? 'active' : ''}" data-sync="vid-model-select" data-index="${i}" data-val="${m}">
                                ${m} <span class="chip-del" data-action="del-vid-model" data-index="${i}" data-midx="${mIdx}">×</span>
                            </div>
                        `).join('')}
                        ${(p.available_models || []).length === 0 ? '<span class="empty-hint">暂无模型，请在下方添加</span>' : ''}
                    </div>
                    <div style="display:flex; gap:10px;">
                        <input type="text" class="input-glass" id="new-model-vid-${i}" placeholder="输入视频模型名称" style="flex:1;">
                        <button data-action="add-vid-model" data-index="${i}" class="btn-glass-secondary">添加模型</button>
                    </div>
                </div>

                <div class="form-group"><label>请求超时</label><input type="number" class="input-glass" value="${p.timeout}" data-sync="vid-time" data-index="${i}"></div>
                <div class="form-group full-width"><label>API Keys</label><textarea class="input-glass" rows="1" data-sync="vid-keys" data-index="${i}">${p.api_keys}</textarea></div>
            </div>
        </div>
    `).join('');
    document.getElementById("video-providers-container").innerHTML = html;
}

function setupEventDelegation() {
    const fileInput = document.getElementById('hidden-file-input');
    
    const animateAdd = (containerId) => {
        setTimeout(() => {
            const container = document.getElementById(containerId);
            const el = container.lastElementChild;
            if(el) {
                el.classList.add('node-enter');
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 10);
    };

    const animateDel = (containerId, stateArray, index, renderFn, callback) => {
        const container = document.getElementById(containerId);
        const el = container.children[index];
        if (el) {
            el.classList.add('node-exit');
            setTimeout(() => {
                stateArray.splice(index, 1);
                renderFn();
                if(callback) callback();
            }, 300);
        }
    };

    document.body.addEventListener('click', (e) => {
        const navItem = e.target.closest('.nav-item');
        if (navItem) {
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            navItem.classList.add('active');
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.getElementById(navItem.getAttribute('data-target')).classList.add('active');
            return;
        }

        const chip = e.target.closest('.selector-chip');
        if (chip) {
            const inputId = chip.getAttribute('data-input');
            document.getElementById(inputId).value = chip.getAttribute('data-id');
            document.querySelectorAll(`.selector-chip[data-input="${inputId}"]`).forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            return;
        }

        const apiChip = e.target.closest('.api-chip');
        if (apiChip && !e.target.closest('.chip-del')) {
            const sync = apiChip.getAttribute('data-sync');
            const idx = parseInt(apiChip.getAttribute('data-index'), 10);
            const val = apiChip.getAttribute('data-val');
            
            if (sync === 'prov-api') { state.providers[idx].api_type = val; renderProviders(); } 
            else if (sync === 'vid-api') { state.video_providers[idx].api_type = val; renderVideoProviders(); }
            else if (sync === 'prov-model-select') { state.providers[idx].model = val; renderProviders(); } 
            else if (sync === 'vid-model-select') { state.video_providers[idx].model = val; renderVideoProviders(); }
            return;
        }

        if (e.target.closest('#persona-upload-trigger')) fileInput.click();

        // 🔴 终极修复：使用 [data-action] 而不仅是 button，这样就能拦截到属于 <span> 的关闭按钮了！
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const act = btn.getAttribute('data-action');
        const idx = parseInt(btn.getAttribute('data-index'), 10);

        if (act === 'save-config') saveConfig(btn);
        
        if (act === 'add-preset') { state.presets.push({name:"", prompt:""}); renderPresets(); animateAdd('presets-container'); }
        if (act === 'del-preset') { animateDel('presets-container', state.presets, idx, renderPresets); }
        
        if (act === 'add-provider') { state.providers.push({id:`node_${state.providers.length+1}`, api_type:"openai_image", base_url:"", model:"", available_models:[], api_keys:"", timeout:60}); renderProviders(); renderSelectors(); animateAdd('providers-container'); }
        if (act === 'del-provider') { animateDel('providers-container', state.providers, idx, renderProviders, renderSelectors); }
        
        if (act === 'add-video-provider') { state.video_providers.push({id:`v_node_${state.video_providers.length+1}`, api_type:"async_task", base_url:"", model:"", available_models:[], api_keys:"", timeout:300}); renderVideoProviders(); renderSelectors(); animateAdd('video-providers-container'); }
        if (act === 'del-video-provider') { animateDel('video-providers-container', state.video_providers, idx, renderVideoProviders, renderSelectors); }
        
        if (act === 'del-persona-img') { animateDel('persona-upload-container', state.persona_config.persona_ref_image, idx, renderPersonaImages); }

        if (act === 'add-prov-model') {
            const input = document.getElementById(`new-model-img-${idx}`);
            const newModel = input.value.trim();
            if(newModel && !state.providers[idx].available_models.includes(newModel)) {
                state.providers[idx].available_models.push(newModel);
                if(!state.providers[idx].model) state.providers[idx].model = newModel;
                renderProviders();
            }
        }
        if (act === 'add-vid-model') {
            const input = document.getElementById(`new-model-vid-${idx}`);
            const newModel = input.value.trim();
            if(newModel && !state.video_providers[idx].available_models.includes(newModel)) {
                state.video_providers[idx].available_models.push(newModel);
                if(!state.video_providers[idx].model) state.video_providers[idx].model = newModel;
                renderVideoProviders();
            }
        }
        if (act === 'del-prov-model') {
            e.stopPropagation(); // 防止触发父级 div 的点击事件
            const mIdx = parseInt(btn.getAttribute('data-midx'), 10);
            const removed = state.providers[idx].available_models.splice(mIdx, 1)[0];
            if(state.providers[idx].model === removed) state.providers[idx].model = state.providers[idx].available_models[0] || "";
            renderProviders();
        }
        if (act === 'del-vid-model') {
            e.stopPropagation();
            const mIdx = parseInt(btn.getAttribute('data-midx'), 10);
            const removed = state.video_providers[idx].available_models.splice(mIdx, 1)[0];
            if(state.video_providers[idx].model === removed) state.video_providers[idx].model = state.video_providers[idx].available_models[0] || "";
            renderVideoProviders();
        }
    });

    fileInput.addEventListener('change', function(e) {
        const files = Array.from(e.target.files);
        if (!files.length) return;
        let loadedCount = 0;
        if (!state.persona_config.persona_ref_image) state.persona_config.persona_ref_image = [];
        files.forEach(file => {
            const reader = new FileReader();
            reader.onload = function(evt) {
                state.persona_config.persona_ref_image.push(evt.target.result);
                loadedCount++;
                if (loadedCount === files.length) {
                    renderPersonaImages();
                    showToast(`成功提取 ${files.length} 张图片`);
                }
            };
            reader.readAsDataURL(file);
        });
        fileInput.value = '';
    });

    // 🔴 终极修复：为了防止因为模型删改导致 UI 重绘（renderProviders）时把未保存的文字清空，
    // 我们必须保留这个双向绑定的实时同步机制！
    document.body.addEventListener('input', (e) => {
        const input = e.target;
        if (!input.hasAttribute('data-sync')) return;
        const s = input.getAttribute('data-sync');
        const i = parseInt(input.getAttribute('data-index'), 10);
        const v = input.value;
        
        if (s === 'preset-name') state.presets[i].name = v;
        if (s === 'preset-prompt') state.presets[i].prompt = v;
        
        if (s === 'prov-id') { state.providers[i].id = v; renderSelectors(); }
        if (s === 'prov-url') state.providers[i].base_url = v;
        if (s === 'prov-time') state.providers[i].timeout = v;
        if (s === 'prov-keys') state.providers[i].api_keys = v;
        
        if (s === 'vid-id') { state.video_providers[i].id = v; renderSelectors(); }
        if (s === 'vid-url') state.video_providers[i].base_url = v;
        if (s === 'vid-time') state.video_providers[i].timeout = v;
        if (s === 'vid-keys') state.video_providers[i].api_keys = v;
    });

    document.body.addEventListener('change', (e) => {
        const input = e.target;
        if (!input.hasAttribute('data-sync')) return;
        const s = input.getAttribute('data-sync');
        if (s === 'prov-id' || s === 'vid-id') renderSelectors();
    });
}

async function saveConfig(btn) {
    btn.disabled = true;
    const originalText = btn.innerHTML;
    btn.innerHTML = `<span class="spinner">↻</span> 部署中...`;
    
    // 强制读取上方固定表单
    readBasicFields();

    // 开启终极快照捕捉，双保险保证不漏数据
    const getDOMValues = (selector) => Array.from(document.querySelectorAll(selector)).map(el => el.value);
    
    const provIds = getDOMValues('[data-sync="prov-id"]');
    const provUrls = getDOMValues('[data-sync="prov-url"]');
    const provTimes = getDOMValues('[data-sync="prov-time"]');
    const provKeys = getDOMValues('[data-sync="prov-keys"]');
    state.providers.forEach((p, i) => {
        if(provIds[i] !== undefined) p.id = provIds[i];
        if(provUrls[i] !== undefined) p.base_url = provUrls[i];
        if(provTimes[i] !== undefined) p.timeout = parseFloat(provTimes[i]) || 60;
        if(provKeys[i] !== undefined) p.api_keys = provKeys[i];
    });

    const vidIds = getDOMValues('[data-sync="vid-id"]');
    const vidUrls = getDOMValues('[data-sync="vid-url"]');
    const vidTimes = getDOMValues('[data-sync="vid-time"]');
    const vidKeys = getDOMValues('[data-sync="vid-keys"]');
    state.video_providers.forEach((p, i) => {
        if(vidIds[i] !== undefined) p.id = vidIds[i];
        if(vidUrls[i] !== undefined) p.base_url = vidUrls[i];
        if(vidTimes[i] !== undefined) p.timeout = parseFloat(vidTimes[i]) || 300;
        if(vidKeys[i] !== undefined) p.api_keys = vidKeys[i];
    });

    const payload = {
        ...state,
        presets: state.presets.filter(p=>p.name).map(p=>`${p.name}:${p.prompt}`)
    };

    try {
        const res = await bridge.apiPost("save_config", payload);
        if (res.success) showToast("部署成功，已生效！");
        else showToast("部署异常", "error");
    } catch(e) { showToast("网络错误", "error"); }
    
    setTimeout(() => { btn.disabled = false; btn.innerHTML = originalText; }, 800);
}

init();
