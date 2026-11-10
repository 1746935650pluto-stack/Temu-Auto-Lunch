/* Shared account/shop proportions, rendered locally from the loaded report. */
(() => {
  'use strict';
  const colors = ['#2f6fed','#0c987f','#e69520','#8954ce','#d64c75','#1795b3','#98713e','#506ab0','#6d9c32','#bf6339','#6c5bba','#d87926','#248963','#a94378','#377fb1','#8b8932','#aa5844','#525cc2','#227b82','#a168ba'];
  const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  function shares(entries) {
    const rows = entries.map(row => ({...row, value: number(row.value)}));
    const total = rows.reduce((sum, row) => sum + row.value, 0);
    if (!Number.isFinite(total)) throw new Error('统计总量超出可展示范围');
    const rounded = rows.map((row, index) => {
      const exact = total ? row.value / total * 1000 : 0;
      return {index, units: Math.floor(exact), fraction: exact - Math.floor(exact)};
    });
    let remainder = total ? 1000 - rounded.reduce((sum, row) => sum + row.units, 0) : 0;
    for (const row of [...rounded].sort((a,b) => b.fraction - a.fraction || a.index - b.index)) {
      if (remainder-- <= 0) break;
      row.units++;
    }
    return {total, rows: rows.map((row, index) => ({...row, share: total ? row.value / total : 0,
      percentage: (rounded[index].units / 10).toFixed(1) + '%'}))};
  }
  function color(key) {
    let hash = 0;
    for (const char of String(key)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    return colors[hash % colors.length];
  }
  function render(container, {entries, label, description, onSelect, onDescribe, selected = ''}) {
    const {total, rows} = shares(entries);
    const node = (tag, cls, text) => { const element = document.createElement(tag); if (cls) element.className = cls; if (text !== undefined) element.textContent = text; return element; };
    const svgNode = (tag, attrs = {}, text) => {
      const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attrs).forEach(([key,value]) => element.setAttribute(key,String(value)));
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const root = node('div','proportion-chart'), figure = node('div','proportion-figure');
    const svg = svgNode('svg',{viewBox:'0 0 320 320',width:320,height:320,role:'group','aria-label':description});
    svg.append(svgNode('circle',{cx:160,cy:160,r:105,fill:'none',stroke:'#edf2f8','stroke-width':42}));
    let angle = -Math.PI / 2;
    const point = (radius, a) => [160 + radius * Math.cos(a),160 + radius * Math.sin(a)];
    const caption = row => `${row.label} · ${label} ${row.value.toLocaleString('zh-CN')} · 占比 ${row.percentage}`;
    const choose = row => onSelect?.(row);
    const describe = row => onDescribe?.(caption(row));
    for (const row of rows) {
      if (!row.value) continue;
      const end = angle + row.share * Math.PI * 2, paint = row.color || color(row.key);
      const interactive=onSelect&&row.selectable!==false;
      const group = svgNode('g',{tabindex:0,role:interactive?'button':'img','aria-label':caption(row),'data-share-key':row.key});
      group.append(svgNode('title',{},caption(row)));
      if (row.share === 1) group.append(svgNode('circle',{cx:160,cy:160,r:105,fill:'none',stroke:paint,'stroke-width':42}));
      else {
        const [a,b] = point(126,angle),[c,d] = point(126,end),[e,f] = point(84,end),[g,h] = point(84,angle), large = row.share > .5 ? 1 : 0;
        group.append(svgNode('path',{d:`M ${a} ${b} A 126 126 0 ${large} 1 ${c} ${d} L ${e} ${f} A 84 84 0 ${large} 0 ${g} ${h} Z`,fill:paint,stroke:'#fff','stroke-width':1.5}));
      }
      if (selected && selected !== String(row.key)) group.setAttribute('opacity','.35');
      group.addEventListener('mouseenter',()=>describe(row));group.addEventListener('focus',()=>describe(row));
      if (interactive) {
        group.style.cursor='pointer';group.addEventListener('click',()=>choose(row));
        group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(row);}});
      }
      svg.append(group);angle=end;
    }
    svg.append(svgNode('text',{x:160,y:154,'text-anchor':'middle',fill:'#24334b','font-size':total?27:19,'font-weight':650},total?total.toLocaleString('zh-CN'):'暂无占比'),
      svgNode('text',{x:160,y:180,'text-anchor':'middle',fill:'#94a3b8','font-size':12},total?`${label}合计`:'当前指标均为 0'));
    figure.append(svg);root.append(figure);
    const legend = node('div','proportion-list');
    for (const row of rows) {
      const interactive=onSelect&&row.selectable!==false;
      const item = node(interactive?'button':'div','proportion-row'+(selected&&selected===String(row.key)?' is-selected':''));
      if (interactive) item.type='button';
      item.setAttribute('data-share-legend',String(row.key));item.setAttribute('aria-label',caption(row));item.title=caption(row);
      const swatch = node('i','proportion-swatch');swatch.style.backgroundColor=row.color||color(row.key);
      item.append(swatch,node('span','proportion-name',row.label),node('strong','proportion-value',row.value.toLocaleString('zh-CN')),node('span','proportion-percent',row.percentage));
      item.addEventListener('mouseenter',()=>describe(row));item.addEventListener('focus',()=>describe(row));
      if(interactive)item.addEventListener('click',()=>choose(row));
      legend.append(item);
    }
    root.append(legend);container.replaceChildren(root);
    return {total,rows};
  }
  window.proportionCharts={render,shares,color,colors};
})();
