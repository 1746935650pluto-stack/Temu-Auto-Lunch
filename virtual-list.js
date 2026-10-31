/* Variable-height windowing. Data and selection stay with the caller. */
(() => {
  class SharedVirtualList {
    constructor(node, {createView, estimateHeight, onViews, threshold = 80}) {
      this.node = node;
      this.createView = createView;
      this.estimateHeight = estimateHeight;
      this.onViews = onViews;
      this.threshold = threshold;
      this.rows = [];
      this.cache = new Map();
      this.heights = new Map();
      this.views = new Map();
      this.gaps = new Map();
      this.offsets = [0];
      this.index = new Map();
      this.frame = null;
      this.rendering = false;
      this.node.tabIndex = 0;
      this.node.setAttribute('role', 'list');
      this.schedule = () => {
        if (this.frame !== null) return;
        this.frame = window.requestAnimationFrame(() => {this.frame = null; this.render();});
      };
      for (const event of ['scroll', 'focusin', 'focusout']) node.addEventListener(event, this.schedule, {passive:true});
      this.observer = typeof ResizeObserver === 'function' ? new ResizeObserver(this.schedule) : null;
      this.observer?.observe(node);
      window.addEventListener?.('resize', this.schedule);
    }
    viewport() { return this.node.clientHeight || Math.min(window.innerHeight * .65 || 600, 700); }
    buildOffsets() {
      this.offsets = [0];
      for (const row of this.rows) this.offsets.push(this.offsets.at(-1) + (this.heights.get(row.key) || this.estimateHeight(row)));
    }
    indexAt(y) {
      let low = 0, high = this.rows.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (this.offsets[middle + 1] <= y) low = middle + 1; else high = middle;
      }
      return Math.min(low, Math.max(0, this.rows.length - 1));
    }
    anchor() {
      const index = this.indexAt(this.node.scrollTop);
      return {key:this.rows[index]?.key, delta:this.node.scrollTop - this.offsets[index]};
    }
    restore(anchor) {
      const index = this.index.get(anchor?.key);
      if (index !== undefined) this.node.scrollTop = Math.max(0, this.offsets[index] + anchor.delta);
    }
    setRows(rows, {reset = false} = {}) {
      const anchor = reset ? null : this.anchor();
      this.rows = rows;
      this.index = new Map(rows.map((row, index) => [row.key, index]));
      const focused = document.activeElement;
      for (const [key, view] of this.cache) if (!this.index.has(key)) {
        if (view.line.contains(focused)) this.node.focus({preventScroll:true});
        this.observer?.unobserve(view.slot || view.line);
        (view.slot || view.line).remove();
        this.cache.delete(key); this.heights.delete(key);
      }
      this.buildOffsets();
      if (reset) this.node.scrollTop = 0; else this.restore(anchor);
      this.render();
    }
    render() {
      if (this.rendering) return;
      this.rendering = true;
      try {
        const anchor = this.anchor(), focused = document.activeElement;
        const focusedEntry = [...this.views].find(([, view]) => view.line.contains(focused));
        const width = this.node.clientWidth;
        if (width && this.width && width !== this.width) {this.heights.clear();this.buildOffsets();this.restore(anchor);}
        if (width) this.width = width;
        const windowed = this.rows.length > this.threshold;
        this.node.classList.toggle('shared-windowed', windowed);
        const viewport = this.viewport(), total = this.offsets.at(-1);
        // A deleted last page must not leave the viewport below the list.
        this.node.scrollTop = Math.min(this.node.scrollTop, Math.max(0, total - viewport));
        const top = this.node.scrollTop;
        const overscan = Math.max(450, viewport);
        const start = windowed ? this.indexAt(Math.max(0, top - overscan)) : 0;
        const end = windowed ? Math.min(this.rows.length, this.indexAt(top + viewport + overscan) + 1) : this.rows.length;
        const indexes = new Set();
        for (let index = start; index < end; index++) indexes.add(index);
        // Keep keyboard focus and its neighbours mounted, even after a wheel
        // scroll. Tab and Shift+Tab can then continue through the whole page.
        for (const [key, view] of this.views) if (view.line.contains(focused)) {
          const at = this.index.get(key);
          for (let index = Math.max(0, at - 1); index <= Math.min(this.rows.length - 1, at + 1); index++) indexes.add(index);
        }
        const desired = [], views = new Map(), activeGaps = new Map();
        const gap = (from, to) => {
          if (!windowed || from >= to) return;
          let node = this.gaps.get(from);
          if (!node) {node = document.createElement('div');node.className = 'shared-virtual-spacer';node.setAttribute('aria-hidden', 'true');}
          node.style.height = `${this.offsets[to] - this.offsets[from]}px`;
          activeGaps.set(from, {node, to}); desired.push(node);
        };
        let previous = 0;
        for (const index of [...indexes].sort((a, b) => a - b)) {
          const row = this.rows[index]; if (!row) continue;
          gap(previous, index);
          let view = this.cache.get(row.key);
          if (!view) {view = this.createView(row);this.cache.set(row.key, view);}
          view.update(row); views.set(row.key, view);
          let container = view.line;
          if (windowed) {
            if (!view.slot) {view.slot = document.createElement('div');view.slot.className = 'shared-virtual-slot';}
            if (view.line.parentNode !== view.slot) view.slot.append(view.line);
            container = view.slot;
          }
          container.setAttribute('role', 'listitem');
          container.setAttribute('aria-posinset', String(index + 1));
          container.setAttribute('aria-setsize', String(this.rows.length));
          desired.push(container); previous = index + 1;
        }
        gap(previous, this.rows.length);
        const wanted = new Set(desired);
        for (const child of [...this.node.children]) if (!wanted.has(child)) child.remove();
        desired.forEach((child, index) => {
          if (this.node.children[index] !== child) this.node.insertBefore(child, this.node.children[index] || null);
        });
        for (const [key, view] of this.views) if (!views.has(key)) this.observer?.unobserve(view.slot || view.line);
        this.views = views;
        this.gaps = new Map([...activeGaps].map(([from, value]) => [from, value.node]));
        if (!this.rows.length) {
          const empty = document.createElement('p');empty.className = 'live-empty';empty.textContent = '暂无符合条件的记录';
          this.node.replaceChildren(empty);
        }
        let measured = false;
        for (const [key, view] of views) {
          const container = windowed ? view.slot : view.line;
          if (windowed) this.observer?.observe(container);
          const height = windowed ? container.getBoundingClientRect().height : 0;
          if (height > 0 && Math.abs(height - (this.heights.get(key) || 0)) > .5) {this.heights.set(key, height);measured = true;}
        }
        if (measured) {
          this.buildOffsets();
          for (const [from, {node, to}] of activeGaps) node.style.height = `${this.offsets[to] - this.offsets[from]}px`;
          this.restore(anchor);
          // The measured heights may expose more rows than the estimates did.
          this.schedule();
        }
        if (focused && this.node.contains(focused) && !focused.disabled && document.activeElement !== focused) {
          focused.focus({preventScroll:true});
        } else if (focusedEntry && document.activeElement !== focused) {
          // A status update may replace an action bar or disable its focused
          // button. Recover the same action in the same row when it still
          // exists; never move keyboard activation to a different action.
          const view = views.get(focusedEntry[0]);
          if (view) {
            const label = focused.getAttribute?.('aria-label') || focused.textContent;
            const replacement = [...view.line.querySelectorAll('button,input,select,textarea,a,[tabindex]')]
              .find(node => !node.disabled && !node.hidden && node.tagName === focused.tagName
                && (node.getAttribute?.('aria-label') || node.textContent) === label
                && (!node.getClientRects || node.getClientRects().length));
            if (replacement) replacement.focus({preventScroll:true});
            else {view.line.tabIndex = -1;view.line.focus({preventScroll:true});}
          } else this.node.focus({preventScroll:true});
        }
        this.onViews(views);
      } finally {this.rendering = false;}
    }
    destroy() {
      if (this.frame !== null) window.cancelAnimationFrame(this.frame);
      for (const event of ['scroll', 'focusin', 'focusout']) this.node.removeEventListener(event, this.schedule);
      window.removeEventListener?.('resize', this.schedule);
      this.observer?.disconnect(); this.cache.clear(); this.views.clear();
    }
  }
  window.SharedVirtualList = SharedVirtualList;
})();
