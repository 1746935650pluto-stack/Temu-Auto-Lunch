'use strict';

// Small in-memory DOM for exercising the shipped event handlers without a
// browser or a live workbench. Nodes retain identity, focus and listener state.
function createDocument() {
  const registry = new Map();
  const document = {activeElement:null, created:0};
  class Node {
    constructor(tag='div') {
      document.created++;
      this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;
      this.dataset={};this.attrs={};this.events={};this.value='';this.hidden=false;
      this.disabled=false;this.checked=false;this.className='';this.scrollTop=0;
      this._text='';this.replaceCount=0;this.style={};
      this.classList={add:(...names)=>{this.className=[...new Set([...this.className.split(' ').filter(Boolean),...names])].join(' ');},
        contains:name=>this.className.split(' ').includes(name),toggle:(name,on)=>{const names=new Set(this.className.split(' ').filter(Boolean));if(on===undefined? !names.has(name):on)names.add(name);else names.delete(name);this.className=[...names].join(' ');}};
    }
    set id(value){this._id=value;if(value)registry.set(value,this);}
    get id(){return this._id || '';}
    get isConnected(){return true;}
    set textContent(value){this._text=String(value);for(const child of this.children)child.parentNode=null;this.children=[];}
    get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
    get options(){return this.children.flatMap(node=>node.tagName==='OPTGROUP'?node.children:[node]).filter(node=>node.tagName==='OPTION');}
    get selectedOptions(){return this.options.filter(node=>node.selected || node.value===this.value);}
    append(...nodes){for(const node of nodes)this.insertBefore(node,null);}
    prepend(...nodes){for(const node of [...nodes].reverse())this.insertBefore(node,this.children[0] || null);}
    insertBefore(node,before){
      if(node===before)return node;
      if(node.parentNode)node.remove();
      const index=before?this.children.indexOf(before):this.children.length;
      if(index<0)throw Error('Invalid insert reference');
      this.children.splice(index,0,node);node.parentNode=this;return node;
    }
    replaceChildren(...nodes){this.replaceCount++;for(const node of this.children)node.parentNode=null;this.children=[];this._text='';this.append(...nodes);}
    remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
    contains(node){return !!node && (node===this || this.children.some(child=>child.contains(node)));}
    setAttribute(name,value){this.attrs[name]=String(value);}
    removeAttribute(name){delete this.attrs[name];if(name==='src')delete this.src;}
    addEventListener(name,fn){(this.events[name] ||= []).push(fn);}
    async dispatch(name,event={}){for(const listener of this.events[name] || [])await listener({target:this,preventDefault(){},...event});}
    focus(){document.activeElement=this;}
    querySelectorAll(selector){const tags=selector.split(',').map(part=>part.trim().toUpperCase());const found=[];const visit=node=>{for(const child of node.children){if(tags.includes(child.tagName))found.push(child);visit(child);}};visit(this);return found;}
  }
  document.createElement=tag=>new Node(tag);
  document.getElementById=id=>registry.get(id) || null;
  document.createNode=(id,tag='div')=>{const node=new Node(tag);node.id=id;return node;};
  document.querySelector=selector=>selector.startsWith('#')?document.getElementById(selector.slice(1)):[...registry.values()].find(node=>node.classList.contains(selector.slice(1))) || null;
  return document;
}
module.exports={createDocument};
