import json
from html import escape

import pandas as pd
import streamlit.components.v1 as components


def table_html(data: pd.DataFrame, columns: list[tuple[str, str]], title: str, *, hierarchy: bool = False, totals: dict | None = None, compact: bool = False) -> str:
    payload = json.dumps(
        {
            "rows": data[[key for key, _ in columns] + (["_id", "_parent", "_depth"] if hierarchy else [])].to_dict(orient="records"),
            "columns": [{"key": key, "label": label} for key, label in columns],
            "hierarchy": hierarchy,
            "totals": totals,
            "compact": compact,
        },
        ensure_ascii=True,
        allow_nan=False,
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        """<!doctype html><html lang="es"><head><meta charset="utf-8">
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font:12px Arial,sans-serif;color:#14375f;background:#fff}
#panel{height:100%;display:flex;flex-direction:column}
.toolbar{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin:0 0 10px;flex-wrap:wrap}
.toolbar button,.toolbar input{font:12px Arial;padding:7px 10px;border:1px solid #dbe6f0;border-radius:6px;background:white;color:#23458f}
.toolbar input{flex:1;min-width:100px}.toolbar button{cursor:pointer}
.viewport{flex:1;min-height:0;overflow:scroll;scrollbar-gutter:stable}
.viewport::-webkit-scrollbar{width:12px;height:14px}
.viewport::-webkit-scrollbar-track{background:#eaf0f8}
.viewport::-webkit-scrollbar-thumb{background:#8298bd;border:3px solid #eaf0f8;border-radius:8px}
table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:12px}
table.compact{width:100%;table-layout:fixed}
table.compact td:first-child{white-space:normal;overflow-wrap:anywhere}
table.compact th button{white-space:normal}
th{position:sticky;top:0;background:#23458f;color:white;z-index:1;text-align:left;padding:0}
th button{background:none;color:white;border:0;width:100%;text-align:left;padding:12px;font-weight:600;white-space:nowrap;cursor:pointer}
th button:focus-visible,.toolbar button:focus-visible{outline:2px solid #f6c400;outline-offset:-2px}
td{padding:9px 12px;border-bottom:1px solid #edf1f6;white-space:nowrap;text-align:right}
td:first-child{text-align:left}tbody tr:hover{background:#f2f6fc}
.tree-toggle{border:0;background:transparent;color:#23458f;cursor:pointer;width:24px;padding:0;font-size:14px}
.tree-root{font-weight:700}
tfoot td{position:sticky;bottom:0;background:#23458f;color:white;font-weight:700;z-index:1;border-bottom:0}
#error{color:#b42318;padding:8px 0}
#error:empty{display:none}
#panel:fullscreen{background:white;padding:20px;display:flex;flex-direction:column}
#panel:fullscreen .viewport{flex:1;height:auto}
</style></head><body><section id="panel" aria-label=\""""
        + escape(title, quote=True)
        + """\">
<div class="toolbar"><input id="search" type="search" placeholder="Buscar en la tabla..." aria-label="Buscar en la tabla">
<button id="download" type="button">Descargar CSV</button><button id="expand" type="button">Ampliar tabla</button></div>
<div class="viewport" tabindex="0" role="region" aria-label="Tabla con desplazamiento horizontal y vertical">
<table><colgroup id="widths"></colgroup><thead><tr id="headers"></tr></thead><tbody id="rows"></tbody><tfoot id="totals"></tfoot></table></div>
<div id="error" role="alert"></div></section>
<script id="table-data" type="application/json">"""
        + payload
        + """</script><script>
"use strict";
const data=JSON.parse(document.getElementById("table-data").textContent);
if(data.compact)document.querySelector("table").classList.add("compact");
let sortKey=null,ascending=true;
const expanded=new Set(data.hierarchy?data.rows.filter(row=>row._depth===0).map(row=>row._id):[]);
const search=document.getElementById("search");
const formatInt=new Intl.NumberFormat("es-CO",{maximumFractionDigits:0});
const formatOne=new Intl.NumberFormat("es-CO",{minimumFractionDigits:1,maximumFractionDigits:1});
const formatTwo=new Intl.NumberFormat("es-CO",{minimumFractionDigits:2,maximumFractionDigits:2});
function visibleRows(){
  const query=search.value.trim().toLocaleLowerCase("es");
  const matches=row=>data.columns.some(col=>String(row[col.key]).toLocaleLowerCase("es").includes(query));
  const compare=(a,b)=>{
    const x=a[sortKey],y=b[sortKey];
    const comparison=typeof x==="number"&&typeof y==="number"?x-y:String(x).localeCompare(String(y),"es",{numeric:true});
    return ascending?comparison:-comparison;
  };
  if(data.hierarchy){
    const included=new Set();
    const byId=new Map(data.rows.map(row=>[row._id,row]));
    if(query)for(const row of data.rows.filter(matches)){
      let item=row;while(item){included.add(item._id);item=byId.get(item._parent);}
    }
    const children=new Map();
    for(const row of data.rows){
      if(!children.has(row._parent))children.set(row._parent,[]);
      children.get(row._parent).push(row);
    }
    const rows=[];
    function visit(parent){
      const siblings=[...(children.get(parent)||[])];
      if(sortKey)siblings.sort(compare);
      for(const row of siblings){
        if(query&&!included.has(row._id))continue;
        rows.push(row);
        if(query||expanded.has(row._id))visit(row._id);
      }
    }
    visit("");return rows;
  }
  const rows=data.rows.filter(matches);
  if(sortKey)rows.sort(compare);
  return rows;
}
function render(){
  const body=document.getElementById("rows");body.replaceChildren();
  for(const row of visibleRows()){
    const tr=document.createElement("tr");
    if(data.hierarchy&&row._depth===0)tr.className="tree-root";
    for(const col of data.columns){
      const td=document.createElement("td"),value=row[col.key];
      td.dataset.key=col.key;td.dataset.value=String(value);
      if(col.key==="calls"||col.key==="contacts"||col.key==="noncontacts"){
        td.textContent=formatInt.format(value);
        const maximum=Math.max(1,...data.rows.map(item=>item[col.key]));
        const percent=Math.max(0,Math.min(100,value/maximum*100));
        td.style.background=`linear-gradient(90deg,#b6e9b0 ${percent}%,transparent ${percent}%)`;
      }else if(col.key==="rate"||col.key==="share"||col.key==="failure_share")td.textContent=formatTwo.format(value)+"%";
      else if(col.key==="tmo"||col.key==="idle")td.textContent=formatOne.format(value);
      else if(data.hierarchy&&col.key==="label"){
        td.style.paddingLeft=(12+row._depth*18)+"px";
        if(data.rows.some(item=>item._parent===row._id)){
          const button=document.createElement("button");button.type="button";button.className="tree-toggle";
          button.textContent=expanded.has(row._id)?"−":"+";
          button.setAttribute("aria-label",(expanded.has(row._id)?"Contraer ":"Expandir ")+value);
          button.setAttribute("aria-expanded",String(expanded.has(row._id)));
          button.addEventListener("click",()=>{if(expanded.has(row._id))expanded.delete(row._id);else expanded.add(row._id);render();});
          td.append(button);
        }
        td.append(document.createTextNode(String(value)));
      }else td.textContent=String(value);
      tr.append(td);
    }
    body.append(tr);
  }
  for(const th of document.querySelectorAll("th")){
    const active=th.dataset.key===sortKey;
    th.setAttribute("aria-sort",active?(ascending?"ascending":"descending"):"none");
    th.querySelector("button").textContent=th.dataset.label+(active?(ascending?" ▲":" ▼"):" ↕");
  }
}
for(const [index,col] of data.columns.entries()){
  const width=document.createElement("col");
  width.style.width=index===0?(col.key==="Campaign Name"||data.hierarchy?"420px":"150px"):(col.key==="calls"||col.key==="contacts"?"180px":"170px");
  if(data.compact)width.style.width=index===0?"65%":"35%";
  document.getElementById("widths").append(width);
  const th=document.createElement("th"),button=document.createElement("button");
  th.scope="col";th.dataset.key=col.key;th.dataset.label=col.label;button.type="button";
  button.addEventListener("click",()=>{ascending=sortKey===col.key?!ascending:true;sortKey=col.key;render();});
  th.append(button);document.getElementById("headers").append(th);
}
if(data.totals){
  const tr=document.createElement("tr");
  for(const col of data.columns){
    const td=document.createElement("td"),value=data.totals[col.key];
    td.dataset.key=col.key;td.dataset.value=String(value);
    if(col.key==="calls"||col.key==="contacts"||col.key==="noncontacts")td.textContent=formatInt.format(value);
    else if(col.key==="rate"||col.key==="share"||col.key==="failure_share")td.textContent=formatTwo.format(value)+"%";
    else if(col.key==="tmo"||col.key==="idle")td.textContent=formatOne.format(value);
    else td.textContent=String(value);
    tr.append(td);
  }
  document.getElementById("totals").append(tr);
}
search.addEventListener("input",render);
document.getElementById("download").addEventListener("click",()=>{
  const quote=value=>'"'+String(value).replace(/"/g,'""')+'"';
  const safe=value=>typeof value==="string"&&/^[=+@\\-\\t\\r]/.test(value)?"'"+value:value;
  const rows=[data.columns.map(col=>quote(col.label)).join(";"),...visibleRows().map(row=>data.columns.map(col=>quote(safe(row[col.key]))).join(";"))];
  const url=URL.createObjectURL(new Blob(["\\ufeff"+rows.join("\\r\\n")],{type:"text/csv;charset=utf-8"}));
  const a=document.createElement("a");a.href=url;a.download="Coppel_tabla.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
const panel=document.getElementById("panel"),expand=document.getElementById("expand"),error=document.getElementById("error");
expand.addEventListener("click",async()=>{
  try{if(document.fullscreenElement)await document.exitFullscreen();else await panel.requestFullscreen();error.textContent="";}
  catch(e){error.textContent="No se pudo ampliar la tabla: "+e.message;}
});
document.addEventListener("fullscreenchange",()=>{expand.textContent=document.fullscreenElement?"Cerrar ampliación":"Ampliar tabla";});
render();
</script></body></html>"""
    )


def render_interactive_table(data: pd.DataFrame, columns: list[tuple[str, str]], title: str, *, height: int = 324, hierarchy: bool = False, totals: dict | None = None, compact: bool = False) -> None:
    components.html(table_html(data, columns, title, hierarchy=hierarchy, totals=totals, compact=compact), height=height, scrolling=False)
