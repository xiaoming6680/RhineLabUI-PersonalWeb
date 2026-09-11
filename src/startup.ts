type EntryOptions = {
  root: HTMLElement;
  unlock: () => Promise<boolean>;
  cancel: () => void;
  start: (silent: boolean) => void;
};

/** Owns the entry gesture, including keyboard focus and failed audio startup. */
export class StartupGate {
  private state: "loading" | "waiting" | "starting" | "error" | "started" = "loading";
  private request = 0;
  private button: HTMLButtonElement;
  private silent: HTMLButtonElement;
  private status: HTMLElement;
  constructor(private options: EntryOptions) {
    const { root } = options;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "进入莱茵生命档案终端");
    root.insertAdjacentHTML("beforeend", '<div class="entry-controls"><button class="entry-start" disabled>正在准备终端…</button><button class="entry-silent" hidden>关闭声音并进入</button><p class="entry-status" role="status">资源就绪后即可进入</p></div>');
    this.button = root.querySelector<HTMLButtonElement>(".entry-start")!;
    this.silent = root.querySelector<HTMLButtonElement>(".entry-silent")!;
    this.status = root.querySelector<HTMLElement>(".entry-status")!;
    root.addEventListener("click", event => {
      event.stopPropagation();
      if ((event.target as Element).closest(".entry-silent")) {
        this.request++;
        options.cancel();
        this.finish(true);
      } else if (this.state === "waiting" || this.state === "error") void this.enter();
    });
    // The focus ring only appears for keyboard users; the scripted focus after
    // loading must not draw a selection box for pointer users.
    root.dataset.input = "pointer";
    root.addEventListener("pointerdown", () => { root.dataset.input = "pointer"; }, { capture: true });
    document.addEventListener("keydown", event => {
      if (root.contains(event.target as Node) || (this.state !== "waiting" && this.state !== "error")) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      event.stopPropagation();
      root.dataset.input = "keyboard";
      this.button.focus({ preventScroll: true });
      void this.enter();
    }, { capture: true });
    root.addEventListener("keydown", event => {
      event.stopPropagation();
      if (["Tab", "Enter", " ", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) root.dataset.input = "keyboard";
      if (event.key === "Tab") {
        const buttons = [this.button, this.silent].filter(button => !button.disabled && !button.hidden);
        if (!buttons.length) { event.preventDefault(); return; }
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        event.preventDefault();
        buttons[(index + (event.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
      }
      // Let native buttons activate on Enter/Space, and never leak this event
      // to the terminal's Enter-to-skip handler.
    });
  }
  get phase() { return this.state; }
  ready() {
    this.state = "waiting";
    const root = this.options.root;
    const line = root.querySelector<HTMLElement>(":scope > span")!;
    // Let the progress line fill and the status fade out before the new text
    // and the controls ease in; the button stays disabled until it is visible.
    root.dataset.entry = "connecting";
    line.classList.add("swapping");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
      if (this.state !== "waiting") return;
      line.textContent = "INTERNAL DATABASE / READY";
      line.classList.remove("swapping");
      root.dataset.entry = "waiting";
      this.button.disabled = false;
      this.button.textContent = "点击进入 →";
      this.status.textContent = "轻触屏幕或按 Enter 开始";
      // Focusing the button would draw a focus ring in browsers that treat a
      // fresh page as keyboard-driven. Pointer users get no focus; Enter and
      // Space are handled below without it.
      if (root.dataset.input === "keyboard") this.button.focus({ preventScroll: true });
    }, reduced ? 0 : 420);
  }
  private async enter() {
    const request = ++this.request;
    this.state = "starting";
    this.options.root.dataset.entry = "starting";
    // aria-disabled preserves keyboard focus while repeated input is ignored.
    this.button.setAttribute("aria-disabled", "true");
    // Decoding usually finishes well within a second; only a slow start shows
    // the preparing state, so a normal entry fades out without a text flicker.
    const preparing = setTimeout(() => {
      if (this.state !== "starting" || request !== this.request) return;
      this.button.textContent = "正在准备声音…";
      this.status.textContent = "准备完成后开始播放";
      this.silent.hidden = false;
    }, 1200);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const unlocked = await Promise.race([
        this.options.unlock(),
        new Promise<boolean>(resolve => { timer = setTimeout(() => resolve(false), 20000); }),
      ]);
      if (request !== this.request) return;
      if (unlocked && !document.hidden) this.finish(false);
      else {
        this.options.cancel();
        this.state = "error";
        this.options.root.dataset.entry = "error";
        this.button.removeAttribute("aria-disabled");
        this.button.textContent = "重试声音并进入 →";
        this.status.textContent = "声音暂未就绪，请重试或无声进入";
        this.silent.hidden = false;
      }
    } catch {
      if (request !== this.request) return;
      this.options.cancel();
      this.state = "error";
      this.options.root.dataset.entry = "error";
      this.button.removeAttribute("aria-disabled");
      this.button.textContent = "重试声音并进入 →";
      this.status.textContent = "声音暂未就绪，请重试或无声进入";
      this.silent.hidden = false;
    } finally { clearTimeout(timer); clearTimeout(preparing); }
  }
  private finish(silent: boolean) {
    if (this.state === "started") return;
    this.state = "started";
    this.options.start(silent);
  }
}
