// 独立候选：全部为合成内存状态，不发送API请求、不处理真实金额。
const rows=document.querySelector('#rows');
const data=[{id:'126',sales:'演示销售甲',old:'120.000',target:'96.000',delta:'−24.000',state:'pending'},{id:'125',sales:'演示销售乙',old:'50.000',target:'60.000',delta:'+10.000',state:'pending'}];
const money=v=>'¥'+v;
function render(){
 rows.replaceChildren();
 for(const [i,row] of data.entries()){
  const el=document.createElement('article');el.className='order-row pending';el.id='order-'+row.id;
  const identity=`<div class="identity"><strong><a href="#order-${row.id}">订单 ${row.id}</a></strong>${row.sales?`<p>销售 · ${row.sales}</p>`:''}</div>`;
  if(row.state==='adjust'||row.state==='keep'){
   const adjusted=row.state==='adjust';
   el.innerHTML=identity+`<div class="result"><span class="tag done">${adjusted?'已调整':'已保持原额'}</span><span>当前核定 <b>${money(adjusted?row.target:row.old)}</b></span><span>${adjusted?'调整差额 '+row.delta:'本次不调整金额'}</span><span class="record">演示操作记录 ${i+21} · 老板 · 刚刚${adjusted?' · 待线下处理':''}</span></div>`;
  }else{
   el.innerHTML=identity+`<div class="figures"><div class="money old"><small>当前核定</small>${money(row.old)}</div><span class="arrow" aria-hidden="true">→</span><div class="money"><small>系统新计算</small>${money(row.target)}</div><span class="delta ${row.delta.startsWith('+')?'up':''}">差额 ${row.delta}</span></div>`;
   if(row.state==='stale'||row.state==='blocked'){
    el.innerHTML+=`<div class="state-message"><span>${row.state==='stale'?'建议版本已变化，请刷新后重新决定。':'收款核对状态已变化，当前不能决定。'}</span><button class="quiet" data-refresh>刷新建议</button></div>`;
   }else{
    const actions=document.createElement('div');actions.className='actions';
    for(const [value,label] of [['adjust','确认调整'],['keep','保持原额']]){
     const b=document.createElement('button');b.textContent=label;b.className=value==='adjust'?'primary':'';
     b.addEventListener('click',()=>{actions.querySelectorAll('button').forEach(x=>x.disabled=true);b.textContent='处理中…';setTimeout(()=>{row.state=value;document.querySelector('#notice').textContent=`订单 ${row.id}：${value==='adjust'?'核定金额已调整，资金仍需线下处理。':'已保持原核定金额。'}`;render();},220)});
     actions.append(b);
    }el.append(actions);
   }
  }
  rows.append(el);
 }
 document.querySelectorAll('[data-refresh]').forEach(b=>b.onclick=refresh);
}
function refresh(){for(const row of data){if(row.state==='stale'||row.state==='blocked')row.state='pending'}document.querySelector('#notice').textContent='已刷新建议，请核对最新金额。';render()}
document.querySelector('#refresh').onclick=refresh;
document.querySelector('#reset').onclick=()=>{data.forEach(r=>r.state='pending');document.querySelector('#notice').textContent='';render()};
document.querySelector('#stale').onclick=()=>{data[0].state='stale';document.querySelector('#notice').textContent='';render()};
document.querySelector('#blocked').onclick=()=>{data[0].state='blocked';document.querySelector('#notice').textContent='';render()};
render();
