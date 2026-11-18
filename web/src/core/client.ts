/**
 * client.ts — WebSocket 客户端（连接、重连、心跳、帧分发）。
 *
 * 契约（docs/INTERFACES.md §4）：
 *   new GameClient(url)
 *   client.connect(); client.send(frame)
 *   client.onFrame = (frame: ServerFrame) => void
 *   client.onStatus = (status: ClientStatus) => void
 *
 * 只负责传输，不做状态合并（那是 state.ts 的职责）。
 */

import type { ClientFrame, ServerFrame } from './types';

export type ClientStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8000;
const PING_INTERVAL_MS = 5000;

export class GameClient {
  private socket: WebSocket | null = null;
  private readonly url: string;
  private closedByUser = false;
  private attempts = 0;
  private pingTimer: number | null = null;
  private reconnectTimer: number | null = null;
  private pingSeq = 0;

  onFrame: (frame: ServerFrame) => void = () => {};
  onStatus: (status: ClientStatus) => void = () => {};

  constructor(url: string) {
    this.url = url;
  }

  connect(): void {
    this.closedByUser = false;
    this.open();
  }

  private open(): void {
    this.onStatus('connecting');
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.onStatus('open');
      this.startPing();
    };

    socket.onmessage = (ev: MessageEvent<string>) => {
      let frame: ServerFrame;
      try {
        frame = JSON.parse(ev.data) as ServerFrame;
      } catch {
        return;
      }
      if (frame && typeof (frame as { t?: unknown }).t === 'string') {
        this.onFrame(frame);
      }
    };

    socket.onclose = () => {
      this.stopPing();
      this.socket = null;
      this.onStatus('closed');
      if (!this.closedByUser) this.scheduleReconnect();
    };

    socket.onerror = () => {
      this.onStatus('error');
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    const delay = Math.min(
      RECONNECT_MAX_MS,
      RECONNECT_BASE_MS * 2 ** Math.min(this.attempts, 4),
    );
    this.attempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByUser) this.open();
    }, delay);
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = window.setInterval(() => {
      this.pingSeq += 1;
      this.send({ t: 'ping', n: this.pingSeq });
    }, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  send(frame: ClientFrame): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(frame));
    return true;
  }

  close(): void {
    this.closedByUser = true;
    this.stopPing();
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }
}

/** 依据当前页面推导 WS 地址（同源 /ws，开发期由 Vite 代理）。 */
export function defaultWsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws`;
}
