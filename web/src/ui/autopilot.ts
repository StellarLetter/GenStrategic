/**
 * autopilot.ts — 「策略」浮动面板（AI_API.md §3 / R6.17）。
 *
 * 职责：只做 DOM 与网络交互，不含任何游戏规则：
 *   - 启动时 `GET /api/ai/strategies` 拉取可用策略（服务端已自动挂载 `ai/` 目录）；
 *   - 点「刷新策略」发 `POST /api/ai/reload` 让服务端**重新扫描** `ai/` 目录（`GET`
 *     只返回启动时那份清单，新增文件必须靠这次重扫才会出现，见 AI_API.md §3）；
 *   - 玩家在**开局前**（或对局中）从下拉框选一个策略 → 发 `autopilot` 帧让该策略接管
 *     自己的座位；选「我自己操作」→ 发 `{strategy: null}` 收回操作权。
 *   - 下拉框只列出**策略名**，显示名用 `label`（缺省等于 name）。
 *
 * 契约：`new AutopilotPanel(root)`，`panel.render(state)` 随每帧状态刷新可见性与文案。
 * 面板是普通浮动层，拖动与位置记忆由 `ui/floating.ts` 负责（在 main.ts 里装配）。
 */

import type { ClientState } from '../core/state';
import { autopilotOf } from '../core/state';

interface StrategyRow {
  name: string;
  label: string;
  description: string;
  source: string;
  builtin: boolean;
}

interface StrategiesResponse {
  directory: string;
  strategies: StrategyRow[];
  errors: { file: string; error: string }[];
  files: string[];
}

/** 「我自己操作」在下拉框里的值（`autopilot` 帧里对应 `strategy: null`）。 */
const MANUAL = '';

export class AutopilotPanel {
  private readonly root: HTMLDivElement;
  private readonly select: HTMLSelectElement;
  private readonly status: HTMLDivElement;
  private readonly reloadButton: HTMLButtonElement;
  private strategies: StrategyRow[] = [];
  /** 自己当前托管的策略名（`null` = 自己操作）。以服务端 `autopilot` 帧回执为准。 */
  private current: string | null = null;
  private loadError: string | null = null;
  private loaded = false;
  /** 已渲染过的「策略 + 错误」指纹，避免每 Tick 重建下拉框。 */
  private lastOptionsFingerprint = '';
  private lastStatusText = '';

  constructor(root: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'autopilot-panel';

    const title = document.createElement('div');
    title.className = 'autopilot-title';
    title.textContent = '策略接管';

    this.select = document.createElement('select');
    this.select.id = 'autopilot-select';
    this.select.title = '选择由哪个策略接管自己的座位；选「我自己操作」收回操作权';
    // 下拉框压在浮动面板里：pointerdown 不能冒泡到拖动握把，否则指针被面板捕获后
    // click 就落不到 select 上（见 ui/floating.ts）。
    this.select.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    this.select.addEventListener('change', () => this.onSelectChange());

    this.reloadButton = document.createElement('button');
    this.reloadButton.type = 'button';
    this.reloadButton.id = 'autopilot-reload';
    this.reloadButton.textContent = '刷新策略';
    this.reloadButton.title = '重新扫描服务端的 ai/ 目录（新增/改完策略文件后点一下）';
    this.reloadButton.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    this.reloadButton.addEventListener('click', (ev) => {
      ev.stopPropagation();
      void this.reload();
    });

    this.status = document.createElement('div');
    this.status.className = 'autopilot-status';

    this.root.append(title, this.select, this.reloadButton, this.status);
    root.appendChild(this.root);
    this.renderOptions();
    void this.fetchList();
  }

  /** 根元素（装配点据此把它做成浮动层）。 */
  get element(): HTMLDivElement {
    return this.root;
  }

  /** 用户选择变化：立刻发 `autopilot` 帧（服务端校验后以回执为准）。 */
  onSelect: (strategy: string | null) => void = () => {};

  private onSelectChange(): void {
    const value = this.select.value;
    const strategy = value === MANUAL ? null : value;
    this.current = strategy;
    this.onSelect(strategy);
    this.syncStatus();
  }

  /** 启动时的清单拉取：`GET /api/ai/strategies` 只读服务端**已扫描**的结果，不重扫。 */
  async fetchList(): Promise<void> {
    await this.request('GET', '/api/ai/strategies');
  }

  /**
   * 「刷新策略」：`POST /api/ai/reload` 让服务端重新扫描 `ai/` 目录后返回新清单。
   *
   * 为什么不能只用 `GET`：`GET` 返回的是 loader 上一次 `scan()` 的报告（启动时那份），
   * 后加进 `ai/` 的文件不会凭空出现在里面 —— 这正是「刷新了却看不到新策略」的原因。
   */
  async reload(): Promise<void> {
    await this.request('POST', '/api/ai/reload');
  }

  /** 发一次请求并把响应装进面板（`method` 决定是否触发服务端重扫）。 */
  private async request(method: 'GET' | 'POST', url: string): Promise<void> {
    this.reloadButton.disabled = true;
    try {
      const res = await fetch(url, { method });
      if (!res.ok) throw new Error(String(res.status));
      this.apply((await res.json()) as StrategiesResponse);
    } catch {
      this.loadError = method === 'POST'
        ? '重新扫描失败（服务端未响应）'
        : '无法读取策略清单（服务端未响应）';
      this.syncStatus();
    } finally {
      this.reloadButton.disabled = false;
    }
  }

  /** 把 `GET /api/ai/strategies` 的响应装进面板（`GET` 与 `POST /api/ai/reload` 同构，共用）。 */
  apply(data: StrategiesResponse): void {
    this.strategies = Array.isArray(data.strategies) ? data.strategies : [];
    const errors = Array.isArray(data.errors) ? data.errors : [];
    // 报错要能直接看到是哪个文件，否则「新策略不出现」时用户无从下手（完整原因在 title）。
    this.loadError = errors.length > 0
      ? `${errors.length} 个策略文件加载失败：${errors.map((e) => e.file).join('、')}`
      : null;
    this.loaded = true;
    if (errors.length > 0) {
      this.status.title = errors.map((e) => `${e.file}: ${e.error}`).join('\n');
    } else {
      this.status.title = data.directory ? `策略目录：${data.directory}` : '';
    }
    this.renderOptions();
    this.syncStatus();
  }

  /** 重建下拉框选项（只在策略清单变化时调用）。 */
  private renderOptions(): void {
    const fp = `${this.strategies.map((s) => s.name).join('|')}#${this.loadError ?? ''}`;
    if (fp === this.lastOptionsFingerprint) return;
    this.lastOptionsFingerprint = fp;

    const options: HTMLOptionElement[] = [];
    const manual = document.createElement('option');
    manual.value = MANUAL;
    manual.textContent = '我自己操作';
    options.push(manual);

    for (const s of this.strategies) {
      const opt = document.createElement('option');
      opt.value = s.name;
      opt.textContent = s.label || s.name;
      opt.title = [s.description, s.source ? `来源：${s.source}` : ''].filter(Boolean).join('\n');
      options.push(opt);
    }
    this.select.replaceChildren(...options);
    this.select.value = this.current ?? MANUAL;
  }

  /**
   * 每帧刷新：显示/隐藏、把「自己当前的接管策略」同步到下拉框。
   *
   * `state.me` 为 null（观战）时整个面板隐藏 —— 观战者没有座位可托管。
   */
  render(state: ClientState): void {
    const visible = state.me !== null && !state.over;
    this.root.hidden = !visible;
    if (!visible) return;

    // 服务端回执：只有「自己这个座位」的托管状态才同步到下拉框。
    const mine = autopilotOf(state, state.me);
    if (mine !== this.current) {
      this.current = mine;
      this.select.value = mine ?? MANUAL;
      this.syncStatus();
    }
  }

  /** 更新状态行（只在文案变化时写 DOM，避免每 Tick 触碰布局）。 */
  private syncStatus(): void {
    // 带上「共 N 个策略」，让「刷新策略」的结果（数量变化）一眼可见。
    const total = `共 ${this.strategies.length} 个策略`;
    let text: string;
    if (!this.loaded) text = '正在读取策略…';
    else if (this.loadError) text = this.loadError;
    else if (this.current) {
      const row = this.strategies.find((s) => s.name === this.current);
      text = `当前：${row?.label || this.current} · ${total}`;
    } else text = `当前：你自己操作 · ${total}`;

    if (text === this.lastStatusText) return;
    this.lastStatusText = text;
    this.status.textContent = text;
    this.status.classList.toggle('is-error', Boolean(this.loadError));
  }
}
