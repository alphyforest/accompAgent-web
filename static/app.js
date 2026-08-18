const API_BASE = '/api';

// 合法的英文情绪标签（文件名即标签，均为 png）
// sharing 为事件专用情绪，thinking 为前端思考中状态（非情绪）
const VALID_EMOTIONS = ['happy', 'sad', 'idle', 'surprised', 'embarrassed', 'greet', 'sharing'];

// 情绪标签 -> 展示用 emoji
const EMOJI_MAP = {
  happy: '😊', sad: '😢', idle: '😌', surprised: '😲',
  embarrassed: '😳', greet: '👋', sharing: '💬',
};

// 平静(idle)时交替使用 greet 与 idle（打招呼 + 待机轮换）
const IDLE_PORTRAITS = ['greet', 'idle'];

// 角色立绘清单（控制台预览用，可扩展）
const PORTRAIT_LIST = VALID_EMOTIONS.map((e) => ({
  name: e,
  label: e,
  src: 'assets/character/' + e + '.png',
}));

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
  { id: 'records', label: '记录', icon: '📜' },
  { id: 'settings', label: '设置', icon: '⚙️' },
];

// 打字机速度（每字符 ms）
const TYPE_SPEED = 70;
// 气泡自动消失时间（ms）
const BUBBLE_LIFETIME = 6000;

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
      chatVisible: false,
      _msgId: 0,
      _typeTimers: {},
    };
  },
  computed: {
    currentBackground() {
      return this.backgrounds[this.backgroundIndex].src;
    },
    currentPortrait() {
      // 思考中：用户输入后、模型生成期间
      if (this.streaming) {
        return 'assets/character/thinking.png';
      }
      // 控制台预览优先
      if (this.consoleOpen) {
        return 'assets/character/' + this.previewPortrait + '.png';
      }
      // 平静/待机：greet 与 idle 交替
      if (this.emotion === 'idle') {
        const base = IDLE_PORTRAITS[this.idleFrame % IDLE_PORTRAITS.length];
        return 'assets/character/' + base + '.png';
      }
      // 其余情绪：标签即文件名
      if (VALID_EMOTIONS.includes(this.emotion)) {
        return 'assets/character/' + this.emotion + '.png';
      }
      return 'assets/character/idle.png';
    },
    portraitList() {
      return PORTRAIT_LIST;
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
  },
  mounted() {
    this.refreshMood();
    this.bgm = new Audio(BGMS[this.bgmIndex].src);
    this.bgm.loop = true;
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
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
        }
        const parsed = this.parseEmotionMark(buffer);
        this.emotion = parsed.emotion;
        // 打字机逐字显示（多段发言按段逐字）
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
    // 打字机效果：真正逐字打出（按字符，含换行停顿）
    async typeText(msg, fullText) {
      msg.typing = true;
      msg.display = '';
      const chars = fullText.split('');
      for (let i = 0; i < chars.length; i += 1) {
        msg.display += chars[i];
        // 换行/标点处稍作停顿，模拟自然节奏
        const ch = chars[i];
        let delay = TYPE_SPEED;
        if (ch === '\n') delay = 140;
        else if (ch === '。' || ch === '，' || ch === '！' || ch === '？' || ch === '～' || ch === '~' || ch === '♪') delay = 70;
        await new Promise((r) => { this._typeTimers[msg.id] = setTimeout(r, delay); });
        // 等待 Vue 完成本轮渲染，确保逐字可见
        await Vue.nextTick();
      }
      msg.typing = false;
      msg.content = fullText;
    },
    // 解析流式响应中的 [[EMOTION:xxx]] 标记
    parseEmotionMark(text) {
      const mark = /^\[\[EMOTION:([A-Za-z]+)\]\]/;
      const match = text.match(mark);
      if (match) {
        const emotion = match[1].toLowerCase();
        const body = text.slice(match[0].length).trim();
        return { emotion: VALID_EMOTIONS.includes(emotion) ? emotion : 'idle', body: body };
      }
      return { emotion: 'idle', body: text };
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
    // 清除聊天记录（仅前端）
    clearChat() {
      this.messages = [];
      this.chatVisible = false;
      Object.values(this._typeTimers).forEach(clearTimeout);
      this._typeTimers = {};
    },
    // 重置对话状态（调用后端 reset）
    async resetAll() {
      this.clearChat();
      this.emotion = 'idle';
      try {
        await request(API_BASE + '/reset', { method: 'POST' });
      } catch (e) { /* ignore */ }
      this.refreshMood();
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
    },
    closeConsole() {
      this.consoleOpen = false;
      this.previewPortrait = 'idle';
    },
  },
}).mount('#app');
