const API_BASE = '/api';

// 情绪标签 -> 展示用 emoji（仅 UI 展示，不参与立绘映射）
const EMOJI_MAP = {
  happy: '😊', sad: '😢', idle: '😌', surprised: '😲',
  embarrassed: '😳', greet: '👋', sharing: '💬',
};

// 平静(idle)时优先交替使用 greet 与 idle（打招呼 + 待机轮换，按下发表存在与否自适应）
const IDLE_PORTRAITS = ['greet', 'idle'];

// 主动发言轮询间隔（ms；后台调度器在用户静默一段时间后才会触发）
const INITIATIVE_POLL_MS = 5000;

// 背景清单（控制台预览用，可扩展）
const BACKGROUNDS = [
  { name: '背景 1', src: 'assets/backgrounds/background.png' },
  { name: '背景 2', src: 'assets/backgrounds/background2.png' },
  { name: '背景 3', src: 'assets/backgrounds/background3.png' },
];

// BGM 清单（控制台显示名字，可扩展）
const BGMS = [
  { name: 'Twilight Woods', src: 'assets/bgm/bgm1 - Twilight Woods_L.ogg' },
  { name: 'Golden Courtyard', src: 'assets/bgm/bgm2 - Golden Courtyard_L.ogg' },
  { name: 'As Before', src: 'assets/bgm/bgm3 - As Before_L.ogg' },
  { name: 'Elysian Realm', src: 'assets/bgm/bgm4 - Elysian Realm_L.ogg' },
  { name: 'Sunrise', src: 'assets/bgm/bgm5 - Sunrise_L.ogg' },
];

// 控制台菜单项（可扩展：新增设置项只需在此追加并对应写一个 tab-page）
const CONSOLE_TABS = [
  { id: 'portrait', label: '立绘', icon: '🎨' },
  { id: 'background', label: '背景', icon: '🖼️' },
  { id: 'music', label: '音乐', icon: '🎵' },
  { id: 'memory', label: '记忆', icon: '🧠' },
];

// AI 记忆分类 -> 中文展示名
const MEMORY_CATEGORY_LABELS = {
  profile: '身份 / 性格',
  interest: '喜好',
  fact: '生活事实',
  boundary: '边界 / 禁忌',
  need: '情感需求',
  event_progress: '事件进度',
};

// 打字机速度（每字符 ms）
const TYPE_SPEED = 70;
// 气泡自动消失时间（ms）
const BUBBLE_LIFETIME = 6000;
// 应用内始终使用单一本地用户（单机单用户形态，user_id 固定 default）
const SESSION_ID = 'default';

// 封装 fetch 工具
async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = '请求失败';
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return response;
}

const { createApp } = Vue;

createApp({
  data() {
    return {
      userInput: '',
      messages: [],
      streaming: false,
      mood: 0,
      moodLabel: 'idle',
      backgroundIndex: 0,
      backgrounds: BACKGROUNDS,
      emotion: 'idle',
      previewPortrait: 'idle',
      idleFrame: 0,
      bgmIndex: 0,
      bgmPlaying: false,
      bgm: null,
      consoleOpen: false,
      activeConsoleTab: 'portrait',
      // 记忆模块内部子页：短期记忆 | 长期记忆
      memorySubtabs: 'short',
      chatVisible: false,
      portraitMap: {},
      defaultEmotion: 'idle',
      memoryGroups: {},
      summaries: [],
      _msgId: 0,
      _typeTimers: {},
    };
  },
  computed: {
    currentBackground() {
      return this.backgrounds[this.backgroundIndex].src;
    },
    currentPortrait() {
      // 立绘映射由后端角色卡下发（改动三：前端只展示，不写死 emotion->路径）
      if (this.streaming) {
        return this.portraitMap.thinking || 'assets/character/thinking.png';
      }
      // 控制台预览优先
      if (this.consoleOpen) {
        return this.lookupPortrait(this.previewPortrait);
      }
      // 平静/待机：greet 与 idle 交替（以下发表存在与否自适应）
      if (this.emotion === 'idle') {
        const bases = IDLE_PORTRAITS.filter((e) => this.portraitMap[e]);
        const base = bases.length ? bases[this.idleFrame % bases.length] : this.defaultEmotion;
        return this.lookupPortrait(base);
      }
      return this.lookupPortrait(this.emotion);
    },
    portraitList() {
      // 控制台立绘预览：以角色卡下发的 portrait_map 为准（不含 thinking 思考态）
      return Object.keys(this.portraitMap)
        .filter((e) => e !== 'thinking')
        .map((name) => ({ name, label: name, src: this.portraitMap[name] }));
    },
    bgmList() {
      return BGMS;
    },
    consoleTabs() {
      return CONSOLE_TABS;
    },
    currentConsoleTab() {
      return CONSOLE_TABS.find((t) => t.id === this.activeConsoleTab) || CONSOLE_TABS[0];
    },
    // 只取 agent 回复作为气泡显示
    agentBubbles() {
      return this.messages.filter((m) => m.role === 'assistant' && m.visible);
    },
    moodWidth() {
      // 映射 -100~100 到 0~100
      return Math.round((this.mood + 100) / 2);
    },
    memoryEmpty() {
      return Object.keys(this.memoryGroups).length === 0;
    },
  },
  mounted() {
    this.refreshMood();
    this.refreshCharacter();
    this.bgm = new Audio(BGMS[this.bgmIndex].src);
    this.bgm.loop = true;
    this.refreshMemory();
    this.refreshSummaries();
    // 主动说话轮询：后台调度器在用户静默一段时间后才会触发
    this.initiativeTimer = setInterval(() => this.pollInitiative(), INITIATIVE_POLL_MS);
  },
  methods: {
    // ===== 对话 =====
    async send() {
      const text = this.userInput.trim();
      if (!text || this.streaming) return;
      this.userInput = '';
      // 用户消息只记录到聊天记录（控制台），不作为气泡显示
      this.addRecord('user', text);

      this.streaming = true;
      this.chatVisible = true;
      const assistant = this.addRecord('assistant', '', true);

      try {
        const response = await request(API_BASE + '/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ input: text, session_id: 'default' }),
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        // 后端一次性返回，这里累积完整响应
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
        }
        const parsed = this.parseEmotionMark(buffer);
        this.emotion = parsed.emotion;
        // 收到完整响应：结束"思考中"态，立绘立即切到对应情绪
        // （currentPortrait 在 streaming=true 时固定显示 thinking.png，
        //   必须在此先置 false，立绘才能在打字机开始前切换到位）
        this.streaming = false;
        // 方案二：后端伪流式，前端对完整文本做流式打印（逐字显示）
        await Vue.nextTick(); // 确保气泡 DOM + ref 已绑定、立绘已切换
        await this.typeText(assistant, parsed.body);
      } catch (e) {
        assistant.content = '（连接失败：' + e.message + '）';
        assistant.display = assistant.content;
      } finally {
        this.streaming = false;
        this.idleFrame += 1;
        this.refreshMood();
        this.scheduleFade();
      }
    },
    // 添加聊天记录，返回消息对象
    addRecord(role, content, typing = false) {
      const msg = {
        id: ++this._msgId,
        role,
        content,
        display: content,
        emotion: '',
        typing,
        visible: true,
        fading: false,
      };
      this.messages.push(msg);
      return msg;
    },
    // 打字机效果：直接操作真实 DOM 逐字插入，彻底绕开 Vue 响应式批处理
    // （Vue 的 {{ }} 插值 + nextTick 在此场景被合并为一次性渲染，故改用 DOM 直写）
    async typeText(msg, fullText) {
      msg.typing = true;
      msg.display = '';
      const el = this.bubbleTextEl(msg.id);
      if (!el) {
        // 兜底：拿不到 DOM 时退化为一次性显示
        msg.display = fullText;
        msg.content = fullText;
        msg.typing = false;
        return;
      }
      el.textContent = '';
      const chars = fullText.split('');
      for (let i = 0; i < chars.length; i += 1) {
        const ch = chars[i];
        // 直接往 DOM 文本节点追加当前字，浏览器下一帧绘制
        el.textContent += ch;
        msg.display = el.textContent;
        // 停顿：换行/标点稍慢
        let delay = TYPE_SPEED;
        if (ch === '\n') delay = 140;
        else if (ch === '。' || ch === '，' || ch === '！' || ch === '？' || ch === '～' || ch === '~' || ch === '♪') delay = 70;
        await this._raf(delay);
      }
      msg.typing = false;
      msg.content = fullText;
    },
    // 用 requestAnimationFrame 实现可中断的帧延迟，确保每字真正渲染一帧
    _raf(delay) {
      return new Promise((resolve) => {
        const t0 = performance.now();
        const tick = (now) => {
          if (now - t0 >= delay) { resolve(); return; }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    },
    // 通过 data-id 查找当前消息对应的气泡文本节点
    bubbleTextEl(id) {
      const spans = this.$refs.bubbleTexts || [];
      for (let i = 0; i < spans.length; i += 1) {
        if (spans[i].dataset.id === String(id)) return spans[i];
      }
      return null;
    },
    // 解析流式响应中的 [[EMOTION:xxx]] 标记（情绪合法性按角色卡立绘下发表校验）
    parseEmotionMark(text) {
      const mark = /^\[\[EMOTION:([A-Za-z]+)\]\]/;
      const match = text.match(mark);
      if (match) {
        const emotion = match[1].toLowerCase();
        const body = text.slice(match[0].length).trim();
        const valid = Object.prototype.hasOwnProperty.call(this.portraitMap, emotion);
        return { emotion: valid ? emotion : this.defaultEmotion, body: body };
      }
      return { emotion: this.defaultEmotion, body: text };
    },
    // 依据角色卡下发表查询情绪对应立绘路径；查不到回退默认情绪，兜底用默认情绪文件名
    lookupPortrait(emotion) {
      const key = this.portraitMap[emotion] ? emotion : this.defaultEmotion;
      return this.portraitMap[key] || 'assets/character/' + key + '.png';
    },
    // 气泡自动淡出
    scheduleFade() {
      this.agentBubbles.forEach((msg) => {
        if (msg.visible && !msg.fading && msg.display) {
          msg.fading = true;
          setTimeout(() => {
            msg.visible = false;
            if (!this.agentBubbles.some((m) => m.visible)) {
              this.chatVisible = false;
            }
          }, BUBBLE_LIFETIME);
        }
      });
    },
    // 删除单条聊天记录
    deleteMessage(id) {
      this.messages = this.messages.filter((m) => m.id !== id);
      if (this.agentBubbles.length === 0) {
        this.chatVisible = false;
      }
    },
    // ===== 状态 =====
    async refreshMood() {
      try {
        const response = await request(API_BASE + '/mood');
        const data = await response.json();
        this.mood = data.mood;
        this.moodLabel = data.label;
      } catch (e) { /* ignore */ }
    },
    // 拉取角色卡下发表（情绪->立绘路径 + 默认情绪），改动三：前端不再写死映射
    async refreshCharacter() {
      try {
        const response = await request(API_BASE + '/character');
        const data = await response.json();
        this.portraitMap = data.portrait_map || {};
        this.defaultEmotion = data.default_emotion || 'idle';
      } catch (e) { /* ignore */ }
    },
    // 轮询主动发言并展示为角色气泡（改动四·第二步）
    async pollInitiative() {
      try {
        const response = await request(API_BASE + '/initiative');
        const items = await response.json();
        for (const item of items) {
          const parsed = this.parseEmotionMark(item);
          this.emotion = parsed.emotion;
          const msg = this.addRecord('assistant', parsed.body, true);
          this.chatVisible = true;
          await Vue.nextTick();
          await this.typeText(msg, parsed.body);
        }
      } catch (e) { /* ignore */ }
    },
    // 清除聊天记录（仅前端）
    clearChat() {
      this.messages = [];
      this.chatVisible = false;
      Object.values(this._typeTimers).forEach(clearTimeout);
      this._typeTimers = {};
    },
    // 重置会话上下文（档1 session：清空本会话短期记忆、buffer 与本会话摘要）
    async resetSession() {
      this.clearChat();
      this.emotion = 'idle';
      await this.postReset('session');
    },
    // 清除所有聊天记录（档2 history：清全部摘要+短期，保留身份/关系记忆）
    async clearAllChats() {
      if (!confirm('将清除所有聊天记录与会话摘要（仍然记得你是谁）。确定吗？')) return;
      this.clearChat();
      this.emotion = 'idle';
      await this.postReset('history');
    },
    // 忘记我（档3 all：彻底清除一切记忆，需二次确认）
    async forgetAll() {
      if (!confirm('将彻底删除全部长期记忆，包括对你的认识。此操作不可恢复，确定吗？')) return;
      if (!confirm('再次确认：真的要忘记你吗？')) return;
      this.clearChat();
      this.emotion = 'idle';
      await this.postReset('all');
    },
    async postReset(level) {
      try {
        await request(API_BASE + '/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ level, session_id: SESSION_ID }),
        });
      } catch (e) { /* ignore */ }
      this.refreshMood();
      this.refreshMemory();
      this.refreshSummaries();
    },
    // ===== AI 记忆（第二阶段） =====
    async refreshMemory() {
      try {
        const response = await request(API_BASE + '/memory');
        const data = await response.json();
        this.memoryGroups = data.groups || {};
      } catch (e) { /* ignore */ }
    },
    async refreshSummaries() {
      try {
        const response = await request(API_BASE + '/summaries');
        this.summaries = await response.json();
      } catch (e) { /* ignore */ }
    },
    async deleteMemory(id) {
      if (!confirm('删除这条记忆？')) return;
      try {
        await request(API_BASE + '/memory/' + id, { method: 'DELETE' });
      } catch (e) { alert('删除失败：' + e.message); }
      this.refreshMemory();
    },
    // 确认：将 AI 猜测标记为已确认（value 不变）
    async confirmMemory(item) {
      await this.correctMemoryValue(item, item.value);
    },
    // 纠正：更新 value 并标记 confirmed=1
    async correctMemory(item) {
      const value = prompt('纠正这条记忆为：', item.value);
      if (value === null || value === '') return;
      await this.correctMemoryValue(item, value);
    },
    async correctMemoryValue(item, value) {
      try {
        await request(API_BASE + '/memory/' + item.id + '/correct', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value }),
        });
      } catch (e) { alert('纠正失败：' + e.message); }
      this.refreshMemory();
    },
    memoryCategoryLabel(category) {
      return MEMORY_CATEGORY_LABELS[category] || category;
    },
    // ===== 音乐 =====
    toggleBgm() {
      if (this.bgmPlaying) {
        this.bgm.pause();
        this.bgmPlaying = false;
      } else {
        this.bgm.play().catch(() => {});
        this.bgmPlaying = true;
      }
    },
    selectBgm(i) {
      if (i === this.bgmIndex && this.bgmPlaying) {
        this.toggleBgm();
        return;
      }
      this.bgmIndex = i;
      this.bgm.pause();
      this.bgm = new Audio(BGMS[i].src);
      this.bgm.loop = true;
      this.bgm.play().catch(() => {});
      this.bgmPlaying = true;
    },
    switchBgm() {
      this.selectBgm((this.bgmIndex + 1) % BGMS.length);
    },
    // ===== 背景 =====
    selectBackground(i) {
      this.backgroundIndex = i;
    },
    switchBackground() {
      this.backgroundIndex = (this.backgroundIndex + 1) % this.backgrounds.length;
    },
    // ===== 控制台 =====
    openConsole() {
      this.consoleOpen = true;
      this.previewPortrait = this.emotion === 'idle' ? 'idle' : this.emotion;
      this.refreshMemory();
      this.refreshSummaries();
    },
    closeConsole() {
      this.consoleOpen = false;
      this.previewPortrait = 'idle';
    },
  },
}).mount('#app');
