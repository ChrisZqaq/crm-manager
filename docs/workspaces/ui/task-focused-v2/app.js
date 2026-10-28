/* Independent in-memory prototype. No API, uploads, storage or business writes. */
const viewer = new URLSearchParams(location.search).get('viewer') === 'warehouse' ? 'warehouse' : 'owner';
const me = viewer === 'owner' ? {name:'林岸',duties:'老板 · 销售'} : {name:'周禾',duties:'仓管 · 销售'};
const seed = [
 {id:'T-2031',customer:'星屿收藏',sales:'林岸',amount:'648.000',product:'星轨漫游收藏盒',quantity:'3 盒',status:'awaiting_payment',payment:'none',account:'门店微信',commission:false,cost:'388.800',margin:'259.200',estimated:'25.920'},
 {id:'T-2032',customer:'拾光商店',sales:'许沐',amount:'960.000',product:'森林来信主题礼盒',quantity:'4 盒',status:'ready_to_ship',payment:'pending',account:'门店微信',commission:false},
 {id:'T-2033',customer:'晴日玩具',sales:'许沐',amount:'540.000',product:'风物集收藏卡',quantity:'6 盒',status:'ready_to_ship',payment:'passed',account:'对公银行',commission:false},
 {id:'T-2034',customer:'一隅卡牌',sales:'林岸',amount:'720.000',product:'月光旅行补充包',quantity:'8 盒',status:'shipped',payment:'passed',account:'门店微信',commission:false,cost:'432.000',margin:'288.000',estimated:'28.800'},
 {id:'T-2035',customer:'青叶收藏',sales:'许沐',amount:'810.000',product:'春日回声典藏盒',quantity:'3 盒',status:'completed',payment:'passed',account:'门店微信',commission:true,cost:'324.000',margin:'486.000',estimated:'48.600'},
 {id:'T-2036',customer:'橙间小铺',sales:'周禾',amount:'360.000',product:'远山手记收藏盒',quantity:'2 盒',status:'ready_to_ship',payment:'problem',account:'门店微信',paused:true,reason:'凭证金额看不清，请补充清晰凭证。',commission:false},
 {id:'T-2037',customer:'鹿鸣收藏',sales:'许沐',amount:'480.000',product:'云间日记主题盒',quantity:'2 盒',status:'shipped',payment:'pending',account:'门店微信',commission:false}
];
let orders = structuredClone(seed), activeFilter='todo', listQuery='', listScope='all', formOpen=null, proof=false, toastTimer;
let homeTab='todo', detailTab='work', dirty=false, shownHash=location.hash||'#home', trendObserver;
const main=document.querySelector('main');
const statusNames={awaiting_payment:'待确认',ready_to_ship:'待出库',shipped:'待收货',completed:'已完成',cancelled:'已取消'};
const paymentNames={none:'未登记收款',pending:'待核对',problem:'待处理',passed:'核对通过'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const scaled=v=>/^\d+(\.\d{1,3})?$/.test(String(v))?BigInt(String(v).split('.')[0])*1000n+BigInt((String(v).split('.')[1]||'').padEnd(3,'0')):null;
const href=(id,action)=>`#order/${id}${action?'/'+action:''}`;
function tasks(o){
 if(o.status==='cancelled')return [];
 const own=o.sales===me.name, result=[];
 if(own&&o.payment==='none')result.push({key:'payment',label:'登记收款',group:'我的销售'});
 if(own&&o.payment==='problem')result.push({key:'correct',label:'补正收款',group:'我的销售'});
 if(own&&o.status==='shipped')result.push({key:'receive',label:'确认收货',group:'我的销售'});
 if(viewer==='owner'&&o.payment==='pending'&&o.account==='门店微信')result.push({key:'verify',label:'核对收款',group:'由我核对'});
 if(viewer==='owner'&&o.status==='completed'&&o.payment==='passed'&&o.commission)result.push({key:'commission',label:'复核提成',group:'由我核对'});
 if(o.status==='ready_to_ship'&&!o.paused&&(viewer==='warehouse'||own))result.push({key:'ship',label:'登记发货',group:own?'我的销售':'共享待发货'});
 return result;
}
function pills(o){return `<span class="pill">${statusNames[o.status]}</span><span class="pill ${o.payment==='problem'?'red':o.payment==='passed'?'good':'warn'}">${paymentNames[o.payment]}</span>${o.paused?'<span class="pill red">暂停出库</span>':''}`;}
function taskRow(o,t){return `<div class="task-row"><div><a class="task-name" href="${href(o.id)}">${o.customer}</a><div class="task-meta"><span>${o.id}</span><span>${o.product}</span><span>${o.sales===me.name?'本人销售':'销售：'+o.sales}</span></div>${o.paused?'<span class="pill red">暂停出库</span>':''}</div><span class="money">¥${o.amount}</span><a class="button primary" href="${href(o.id,t.key)}">${t.label}</a></div>`;}
function todoHome(){
 const rows=orders.flatMap(o=>tasks(o).map(t=>({o,t}))), mine=rows.filter(r=>r.t.group==='我的销售'), other=rows.filter(r=>r.t.group!=='我的销售');
 const third=viewer==='owner'?'由我核对':'共享待发货';
 return `<div class="section-head"><h2>我的待办 <span class="muted">${rows.length}</span></h2></div>
 <div class="overview"><a href="#orders?filter=todo"><div><strong>待我处理</strong><span class="small muted">按当前职责汇总</span></div><span class="count">${rows.length}</span></a><a href="#orders?filter=mine"><div><strong>我的销售</strong><span class="small muted">${mine.length}项待办</span></div><span class="count">${mine.length}</span></a><a href="#orders?filter=${viewer==='owner'?'review':'shared'}"><div><strong>${third}</strong><span class="small muted">${viewer==='owner'?'收款与提成':'有仓管职责的人员共享'}</span></div><span class="count">${other.length}</span></a></div>
 <section class="panel"><div class="section-head"><h2>现在需要处理</h2><span class="small muted">${rows.length}项</span></div>${mine.length?`<h3 class="task-caption">我的销售 <span>${mine.length}</span></h3>${mine.map(r=>taskRow(r.o,r.t)).join('')}`:''}${other.length?`<h3 class="task-caption">${third}<span>${other.length}</span></h3>${other.map(r=>taskRow(r.o,r.t)).join('')}`:''}${!rows.length?'<p class="empty">当前没有需要你处理的事项。</p>':''}</section>
`;
}
function list(){
 const rows=orders.filter(o=>{const ts=tasks(o);const match=activeFilter==='todo'?ts.length:activeFilter==='mine'?o.sales===me.name:activeFilter==='review'?ts.some(t=>t.group==='由我核对'):activeFilter==='shared'?ts.some(t=>t.group==='共享待发货'):true;return match&&(!listQuery||`${o.id}${o.customer}${o.product}`.includes(listQuery))&&(listScope==='all'||o.status===listScope);});
 const filters=[['todo','待我处理'],['mine','我的销售'],[viewer==='owner'?'review':'shared',viewer==='owner'?'由我核对':'共享待发货'],['all','全部可见']];
 return `<header><h1>销售订单</h1><a href="#home" class="button">返回待办</a></header><div class="tabs" aria-label="订单范围">${filters.map(([key,label])=>`<button data-filter="${key}" aria-pressed="${activeFilter===key}">${label}</button>`).join('')}</div>
 <form id="search" class="panel filters"><label class="field">客户、订单或商品<input name="query" value="${esc(listQuery)}" placeholder="搜索订单"></label><label class="field">履约状态<select name="scope"><option value="all">全部</option>${Object.entries(statusNames).map(([v,n])=>`<option value="${v}" ${listScope===v?'selected':''}>${n}</option>`).join('')}</select></label><button class="primary">查询</button></form>
 <div class="section-head"><span class="small muted">${rows.length}笔订单</span></div><div class="orders">${rows.map(o=>`<article class="order-card"><div><h2><a href="${href(o.id)}">${o.customer}</a></h2><p class="order-sub">${o.id} · ${o.sales===me.name?'本人销售':'销售 '+o.sales}</p><p class="order-sub">${o.product} · ${o.quantity}</p></div><span class="money">¥${o.amount}</span><div class="states">${pills(o)}</div><a class="button ${tasks(o).length?'primary':''}" href="${href(o.id,tasks(o)[0]?.key)}">${tasks(o)[0]?.label||'查看订单'}</a></article>`).join('')||'<p class="panel empty">没有匹配的订单。</p>'}</div>`;
}
function actionTitle(key){return {payment:'登记本单收款',correct:'补正收款凭证',verify:'核对本单收款',ship:'填写物流并整单发货',receive:'确认客户收货',commission:'复核本单提成'}[key];}
function form(o,key){
 if(!formOpen||formOpen!==key)return '';
 let fields='';
 if(['payment','correct'].includes(key))fields=`<div class="form-fields"><label class="field">收款账户<select name="account" required><option>门店微信</option><option>对公银行</option></select></label><label class="field">登记金额<input name="amount" inputmode="decimal" type="number" min="0" step="0.001" value="${o.amount}" required></label><div class="field full"><span>收款凭证</span><div class="file-ready"><button type="button" id="pick-proof">选择凭证</button><span id="proof-name" class="small muted">${proof?'演示凭证已选择':'未选择'}</span></div><details><summary class="small muted">文件要求</summary><p class="small muted">实际系统支持JPEG、PNG、WebP；本原型选择按钮只装入合成凭证，不上传文件。</p></details></div><label class="field full" id="difference-field" hidden>差额原因<textarea name="difference"></textarea></label></div>`;
 if(key==='verify')fields=`<div class="read-payment"><div class="section-head"><strong>登记金额</strong><span class="money">¥${o.received||o.amount}</span></div><p>${o.account} · 销售 ${o.sales}</p><button class="text-link" type="button" data-proof="${o.id}">查看演示凭证</button></div><label class="field">核对结果<select name="decision"><option value="passed">核对通过</option><option value="problem">退回销售补正</option></select></label><label class="field subsection" id="reason-field" hidden>问题原因<textarea name="reason" placeholder="说明需要补正的内容"></textarea></label>`;
 if(key==='ship')fields=`<div class="read-payment"><strong>门店仓</strong><p class="small muted">${o.product} · ${o.quantity}</p></div><div class="form-fields"><label class="field">承运方<select name="carrier"><option>顺丰</option><option>跨越</option><option>货拉拉</option></select></label><label class="field">运输编号<input name="tracking" required></label><label class="field">出库运费<input name="freight" type="number" min="0" step="0.001" value="0"></label></div>`;
 if(key==='receive')fields='<p>确认客户已收货后，订单将完成。</p>';
 if(key==='commission')fields=`<div class="section-head"><span>系统计算提成</span><strong class="money big">¥${o.estimated}</strong></div><p class="small muted">订单已完成 · 收款核对通过</p><dl class="facts subsection"><dt>订单收入</dt><dd>¥${o.amount}</dd><dt>实际成本</dt><dd>¥${o.cost}</dd><dt>出库运费</dt><dd>¥${o.freight||'0.000'}</dd><dt>毛利</dt><dd>¥${o.margin}</dd></dl>`;
 return `<form id="current-form" class="inline-form" data-key="${key}">${fields}<p id="form-error" class="error" role="alert"></p><div class="actions"><button type="button" id="close-form">返回</button><button class="primary" type="submit">${{payment:'保存收款凭证',correct:'补正后重新提交',verify:'提交核对结果',ship:'确认整单发货',receive:'确认收货并完成',commission:'确认核定'}[key]}</button></div></form>`;
}
function detail(o){
 const ts=tasks(o),own=o.sales===me.name;if(formOpen&&!ts.some(t=>t.key===formOpen))formOpen=null;
 const title=ts.length?ts.map(t=>t.label).join(' / '):o.status==='completed'?'订单已完成':o.status==='cancelled'?'订单已取消':o.paused?(o.payment==='problem'?'等待销售补正':'等待核对收款'):'等待负责人员处理';
 const lead=o.paused?(o.payment==='problem'?esc(o.reason)+' 出库暂停，核对通过后恢复。':'已重新提交核对；通过后恢复出库。'):ts.some(t=>t.key==='ship')&&o.payment==='pending'?'收款待核对，当前可发货。':ts.some(t=>t.key==='verify')?'请核实到账与凭证。':ts.some(t=>t.key==='payment')?'登记金额与收款凭证。':ts.some(t=>t.key==='receive')?'客户收到货后确认完成。':ts.some(t=>t.key==='commission')?'核对经营结果后核定提成。':o.status==='shipped'?'由销售确认客户收货。':o.status==='ready_to_ship'?'仓管共享待发货。':'';
 return `<a href="#orders" class="back">返回订单列表</a><header class="detail-head"><div><p class="eyebrow">${o.id}</p><h1>${o.customer}</h1><div class="detail-context"><span>${own?'本人销售':'销售：'+o.sales}</span><span>快递</span><span>门店渠道</span></div></div><div><p class="small muted">订单金额</p><p class="money big">¥${o.amount}</p></div></header>
 <div class="identity-status" aria-label="订单当前状态">${pills(o)}${o.status==='completed'?`<span class="pill">${o.commission?'提成待复核':'提成已核定'}</span>`:''}</div>
 ${tabbar('detail',[['work','待我处理'],['data','订单资料'],['history','处理记录']],detailTab)}
 <section ${panelAttrs('detail','work',detailTab==='work')}><div class="panel current"><p class="eyebrow">${ts.length?'当前由我处理':'当前无我的待办'}</p><h2>${title}</h2>${lead?`<p class="lead">${lead}</p>`:''}${!formOpen&&ts.length?`<div class="actions">${ts.map(t=>`<button class="primary" data-open="${t.key}">${t.label}</button>`).join('')}</div>`:''}${ts.map(t=>form(o,t.key)).join('')}${o.result?`<div class="inline-result" role="status">${esc(o.result)}</div>`:''}</div>
 ${own&&!['completed','cancelled'].includes(o.status)?`<details class="panel fold"><summary>更多操作</summary><div class="fold-body"><div class="actions"><button data-entry="修改订单">修改订单</button><button data-entry="调整运费">调整运费</button>${!['shipped','completed'].includes(o.status)?'<button class="danger" id="cancel-order">取消订单</button>':''}</div></div></details>`:''}</section>
 <section ${panelAttrs('detail','data',detailTab==='data')}><section class="panel"><div class="section-head"><h2>商品明细</h2><span class="small muted">1个SKU</span></div><div class="line-item"><div><strong>${o.product}</strong><p class="small muted">SYN-${o.id.slice(2)} · 门店仓</p></div><div><strong>${o.quantity}</strong><p class="small muted">¥${o.amount}</p></div></div>${['shipped','completed'].includes(o.status)?`<dl class="facts subsection"><dt>出库运费</dt><dd>¥${o.freight||'0.000'}</dd>${o.cost?`<dt>实际成本</dt><dd>¥${o.cost}</dd><dt>毛利</dt><dd>¥${o.margin}</dd>`:''}</dl>`:''}</section>
 <section class="panel"><h2>收款记录</h2><div class="subsection">${o.payment==='none'?'<p class="muted">暂无收款凭证。</p>':`<dl class="facts"><dt>账户</dt><dd>${o.account}</dd><dt>登记金额</dt><dd class="money">¥${o.received||o.amount}</dd><dt>核对状态</dt><dd>${paymentNames[o.payment]}</dd><dt>凭证</dt><dd><button type="button" class="text-link" data-proof="${o.id}">查看演示凭证</button></dd>${o.reason?`<dt>问题原因</dt><dd>${esc(o.reason)}</dd>`:''}</dl>`}</div></section>
 <section class="panel"><h2>物流与收货信息</h2><dl class="facts subsection"><dt>收货人</dt><dd>合成收件人</dd><dt>地址</dt><dd>合成城市 · 演示街道，不用于实际发货</dd><dt>物流</dt><dd>${['shipped','completed'].includes(o.status)?`${o.carrier||'顺丰'} · ${esc(o.tracking||'SYN-TRACK-2034')}`:'尚未发货'}</dd></dl></section></section>
 <section ${panelAttrs('detail','history',detailTab==='history')}><section class="panel"><h2>处理记录</h2><ol class="history"><li><time>10月23日 10:20</time><div><strong>订单已创建</strong><p>销售 ${o.sales} · ${o.id}</p></div></li>${o.payment!=='none'?'<li><time>10月23日 10:35</time><div><strong>已登记收款凭证</strong><p>合成演示记录</p></div></li>':''}${['shipped','completed'].includes(o.status)?'<li><time>10月23日 11:00</time><div><strong>整单已发货</strong><p>发货事实保留</p></div></li>':''}${o.result?`<li><time>本次演示</time><div><strong>${esc(o.result)}</strong></div></li>`:''}</ol></section></section>`;
}

function notify(text){clearTimeout(toastTimer);document.querySelector('#toast').textContent=text;toastTimer=setTimeout(()=>document.querySelector('#toast').textContent='',3000);}
async function confirmAction(title,copy){const d=document.querySelector('#confirm');document.querySelector('#confirm-title').textContent=title;document.querySelector('#confirm-copy').textContent=copy;d.returnValue='';d.showModal();return new Promise(resolve=>d.addEventListener('close',()=>resolve(d.returnValue==='ok'),{once:true}));}
function currentOrder(){return orders.find(o=>o.id===location.hash.split('/')[1]);}
function render(){
 trendObserver?.disconnect();
 const route=location.hash.slice(1)||'home';document.querySelector('#person').textContent=me.name;document.querySelector('#duties').textContent=me.duties;
 document.querySelectorAll('[data-nav]').forEach(a=>{if((route.startsWith('home')?'home':'orders')===a.dataset.nav)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
 if(route.startsWith('order/')){const o=currentOrder();main.innerHTML=o?detail(o):'<p class="panel">没有这笔合成订单。<a href="#orders">返回列表</a></p>';document.title=`${o?.id||'订单'} · 阿罗拉原型`;}
 else if(route.startsWith('orders')){const f=route.split('filter=')[1];if(f)activeFilter=f;main.innerHTML=list();document.title='销售订单 · 阿罗拉原型';}
 else{main.innerHTML=home();document.title='工作台 · 阿罗拉原型';}
 bind();bindTabs();bindBusiness();
}
function bind(){
 main.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{activeFilter=b.dataset.filter;history.replaceState(null,'','#orders');render();});
 const search=main.querySelector('#search');if(search)search.onsubmit=e=>{e.preventDefault();const d=new FormData(search);listQuery=d.get('query').trim();listScope=d.get('scope');render();};
 main.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>{formOpen=b.dataset.open;proof=false;dirty=false;detailTab='work';render();main.querySelector('#current-form input,#current-form select,#current-form button')?.focus();});
 const close=main.querySelector('#close-form');if(close)close.onclick=()=>{formOpen=null;dirty=false;render();main.querySelector('[data-open]')?.focus();};
 const pick=main.querySelector('#pick-proof');if(pick)pick.onclick=()=>{proof=true;dirty=true;document.querySelector('#proof-name').textContent='演示凭证已选择';};
 const amount=main.querySelector('[name=amount]');if(amount)amount.oninput=()=>{main.querySelector('#difference-field').hidden=scaled(amount.value)===scaled(currentOrder().amount);};
 const decision=main.querySelector('[name=decision]');if(decision)decision.onchange=()=>{document.querySelector('#reason-field').hidden=decision.value!=='problem';};
 const f=main.querySelector('#current-form');if(f){f.addEventListener('input',()=>dirty=true);f.addEventListener('change',()=>dirty=true);}if(f)f.onsubmit=async e=>{
  e.preventDefault();const o=currentOrder(),key=f.dataset.key,d=new FormData(f),error=main.querySelector('#form-error');
  if(['payment','correct'].includes(key)&&!proof){error.textContent='请选择收款凭证。';pick.focus();return;}
  if(['payment','correct'].includes(key)&&(scaled(d.get('amount'))===null)){error.textContent='金额最多保留3位小数。';f.elements.amount.focus();return;}
  if(['payment','correct'].includes(key)&&scaled(d.get('amount'))!==scaled(o.amount)&&!d.get('difference').trim()){error.textContent='登记金额与订单金额不同，请填写差额原因。';f.elements.difference.focus();return;}
  if(key==='verify'&&d.get('decision')==='problem'&&!d.get('reason').trim()){error.textContent='请填写需要销售补正的原因。';f.elements.reason.focus();return;}
  if(key==='ship'&&!await confirmAction('确认整单发货？','确认后整单扣减库存，并记录实际发货。'))return;
  if(key==='receive'&&!await confirmAction('确认客户已收货？','订单将完成，已发生的发货事实保留。'))return;
  if(key==='commission'&&!await confirmAction(`核定提成 ¥${o.estimated}？`,'本次核定结果将保留记录。'))return;
  if(['payment','correct'].includes(key)){o.payment='pending';o.account=d.get('account');o.received=d.get('amount');if(key==='payment')o.status='ready_to_ship';o.result=key==='correct'?'已补正并重新提交核对；出库仍暂停，等待核对通过。':'收款凭证已保存，订单待出库。';}
  if(key==='verify'){o.payment=d.get('decision');if(o.payment==='problem'){o.reason=d.get('reason').trim();if(o.status!=='shipped'&&o.status!=='completed')o.paused=true;o.result=`已退回销售 ${o.sales} 补正；${o.status==='shipped'?'保留已发货状态。':'出库已暂停。'}`;}else{o.paused=false;o.reason='';o.result='收款核对通过。';}}
  if(key==='ship'){o.status='shipped';o.carrier=d.get('carrier');o.tracking=d.get('tracking');o.freight=d.get('freight');o.result='整单已发货，等待销售确认客户收货。';}
  if(key==='receive'){o.status='completed';o.commission=true;o.result='已确认客户收货，订单完成。';}
  if(key==='commission'){o.commission=false;o.result=`提成 ¥${o.estimated} 已核定。`;}
  formOpen=null;dirty=false;detailTab='work';render();main.querySelector('.current')?.scrollIntoView({block:'start'});notify(o.result);
 };
 const cancel=main.querySelector('#cancel-order');if(cancel)cancel.onclick=async()=>{if(await confirmAction('取消订单并释放占货？','仅取消未发货订单；保留收款凭证，取消不代表已退款。')){const o=currentOrder();o.status='cancelled';dirty=false;o.result='订单已取消，占货已释放；收款凭证保留。';formOpen=null;render();notify(o.result);}};
 main.querySelectorAll('[data-entry]').forEach(b=>b.onclick=()=>notify(`${b.dataset.entry}沿用现有页面；本候选仅展示低频入口。`));
}
window.addEventListener('hashchange',async()=>{
 const next=location.hash;const leaving=shownHash.startsWith('#order/')&&next.split('/')[1]!==shownHash.split('/')[1];
 if(dirty&&leaving){history.replaceState(null,'',shownHash);if(!await confirmAction('离开当前订单？','尚有未保存的填写内容，离开后将丢弃。'))return;dirty=false;location.hash=next;return;}
 shownHash=next;dirty=false;detailTab='work';formOpen=next.split('/')[2]||null;render();window.scrollTo({top:0});
});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
document.querySelector('#theme').onchange=e=>{document.documentElement.dataset.theme=e.target.value;drawTrend();};
document.querySelector('#reset').onclick=()=>{orders=structuredClone(seed);formOpen=null;dirty=false;render();notify('合成演示已重置。');};
formOpen=location.hash.split('/')[2]||null;
render();

console.assert(new Set(seed.map(o=>o.id)).size===seed.length,'Unique synthetic orders');
console.assert(scaled('1.001')===1001n&&scaled('1.0000')===null,'Three decimal input');
console.assert(viewer!=='owner'||orders.filter(o=>o.sales!==me.name).every(o=>tasks(o).every(t=>['verify','commission'].includes(t.key))),'Owner must not operate other sales orders');
console.assert(viewer!=='warehouse'||orders.filter(o=>o.status==='ready_to_ship'&&!o.paused).every(o=>tasks(o).some(t=>t.key==='ship')),'Shared warehouse tasks');

function tabbar(group,items,selected){return `<div class="page-tabs" role="tablist" aria-label="${group==='home'?'工作台内容':'订单内容'}">${items.map(([key,label])=>`<button role="tab" id="${group}-${key}-tab" data-tab="${key}" data-group="${group}" aria-controls="${group}-${key}-panel" aria-selected="${selected===key}" tabindex="${selected===key?'0':'-1'}">${label}</button>`).join('')}</div>`;}
function panelAttrs(group,key,visible){return `role="tabpanel" id="${group}-${key}-panel" aria-labelledby="${group}-${key}-tab" data-panel-group="${group}" ${visible?'':'hidden'}`;}
function home(){return `<header><div><p class="eyebrow">10月23日，星期五</p><h1>工作台</h1></div><a class="button" href="#orders">查看订单</a></header>${tabbar('home',[['todo','我的待办'],['business','经营概况']],homeTab)}<section ${panelAttrs('home','todo',homeTab==='todo')}>${todoHome()}</section><section ${panelAttrs('home','business',homeTab==='business')}>${business()}</section>`;}
function business(){
 const groups=[['awaiting','待收款订单',orders.filter(o=>o.status==='awaiting_payment')],['ready','待出库订单',orders.filter(o=>o.status==='ready_to_ship')],['pending','待确认收款',orders.filter(o=>o.payment==='pending')],[viewer==='owner'?'review':'receipt',viewer==='owner'?'待确认复核订单':'待确认收货',orders.filter(o=>viewer==='owner'?o.status==='completed'&&o.commission:o.status==='shipped')]];
 const orderPreview=rows=>rows.slice(0,2).map(o=>`<a class="metric-row" href="${href(o.id)}"><span>${o.customer}</span><b>¥${o.amount.replace('.000','')}</b></a>`).join('')||'<span class="muted">暂无记录</span>';
 return `<div class="business-grid" aria-label="原工作台业务摘要"><article class="business-card"><div class="card-heading"><h2>今日销售额</h2></div><div class="business-split sales-split"><div><span class="small muted">销售额</span><strong>¥1,530</strong></div><div><span class="small muted">增速</span><strong class="decrease">−13.07%</strong></div></div></article>
 <article class="business-card"><div class="card-heading"><h2>客户</h2><button class="text-link" data-info="customers">全部</button></div><div class="business-split"><div class="metric-left"><strong>128<small>位</small></strong><span class="increase small">今日新增 3</span></div><div class="metric-side"><span class="small muted">本月购买 Top3</span><button class="ranking-preview" data-info="customers" aria-label="查看本月客户购买Top3"><span class="metric-row"><span>青叶收藏</span><b>8,100</b></span><span class="metric-row"><span>星屿收藏</span><b>7,200</b></span><span class="metric-row"><span>拾光商店</span><b>6,400</b></span></button></div></div></article>
 <article class="business-card"><div class="card-heading"><h2>在售商品组合</h2><button class="text-link" data-info="products">全部</button></div><div class="business-split"><div class="metric-left"><strong>6<small>类</small></strong></div><div class="metric-side"><span>星轨 · 简中</span><span>星轨 · 日版</span><span>林野 · 简中</span></div></div></article>
 <article class="business-card"><div class="card-heading"><h2>低库存提醒</h2><button class="text-link" data-info="low">全部</button></div><div class="business-split"><div class="metric-left"><strong>2<small>条</small></strong></div><div class="metric-side"><div class="metric-row"><span>星轨典藏盒</span><b>3盒</b></div><div class="metric-row"><span>林野礼盒</span><b>2盒</b></div></div></div></article>
 ${groups.map(([key,label,rows])=>`<article class="business-card"><div class="card-heading"><h2>${label}</h2><button class="text-link" data-bucket="${key}">查看全部</button></div><div class="business-split"><div class="metric-left"><strong>${rows.length}<small>单</small></strong></div><div class="metric-side">${orderPreview(rows)}</div></div></article>`).join('')}</div>
 <section class="panel trend-panel"><div class="section-head"><h2>近7天已交付销售</h2><span class="small muted">10/17—10/23</span></div><canvas id="trend" role="img" aria-label="近7天已交付销售折线图，数值可通过下方日期按钮或数据表查看"></canvas><div class="trend-days" aria-label="查看每日金额">${trendRows().map(([day,value],i)=>`<button data-day="${i}" style="--i:${i}" aria-pressed="${i===6}" aria-label="10月${day}日，${value}元">10/${day}</button>`).join('')}</div><p id="trend-value" role="status" aria-live="polite">10月23日 · ¥1,530.000</p><details class="chart-data"><summary>查看每日数据</summary><table><thead><tr><th>日期</th><th>已交付销售额</th></tr></thead><tbody>${trendRows().map(([d,v])=>`<tr><td>10月${d}日</td><td>¥${v.toFixed(3)}</td></tr>`).join('')}</tbody></table></details></section>`;
}
function trendRows(){return [[17,520],[18,980],[19,740],[20,1200],[21,860],[22,1760],[23,1530]];}
function selectTab(group,key){
 if(group==='home')homeTab=key;else detailTab=key;
 document.querySelectorAll(`[data-group="${group}"][role=tab]`).forEach(b=>{const on=b.dataset.tab===key;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
 document.querySelectorAll(`[data-panel-group="${group}"]`).forEach(p=>p.hidden=p.id!==`${group}-${key}-panel`);
 if(group==='home'&&key==='business')drawTrend();
}
function bindTabs(){
 main.querySelectorAll('[role=tablist]').forEach(list=>{const tabs=[...list.querySelectorAll('[role=tab]')];tabs.forEach(b=>b.onclick=()=>selectTab(b.dataset.group,b.dataset.tab));list.onkeydown=e=>{const i=tabs.indexOf(document.activeElement);if(i<0)return;let n;if(e.key==='ArrowRight')n=(i+1)%tabs.length;else if(e.key==='ArrowLeft')n=(i+tabs.length-1)%tabs.length;else if(e.key==='Home')n=0;else if(e.key==='End')n=tabs.length-1;else return;e.preventDefault();tabs[n].focus();selectTab(tabs[n].dataset.group,tabs[n].dataset.tab);};});
}
function showInfo(title,body){document.querySelector('#info-title').textContent=title;document.querySelector('#info-body').innerHTML=body;document.querySelector('#info').showModal();}
function bindBusiness(){
 main.querySelectorAll('[data-info]').forEach(b=>b.onclick=()=>{const k=b.dataset.info;if(k==='customers')showInfo('本月客户购买排行','<ol class="info-list"><li>青叶收藏 <strong>¥8,100.000</strong></li><li>星屿收藏 <strong>¥7,200.000</strong></li><li>拾光商店 <strong>¥6,400.000</strong></li></ol><p class="small muted">客户128位，今日新增3位。全部为合成资料。</p>');if(k==='products')showInfo('在售商品组合','<ul class="info-list">'+['星轨 · 简中','星轨 · 日版','林野 · 简中','林野 · 日版','远山 · 英文','远山 · 日版'].map(n=>`<li>${n}</li>`).join('')+'</ul>');if(k==='low')showInfo('低库存提醒','<ul class="info-list"><li>星轨典藏盒 <strong>3盒</strong></li><li>林野礼盒 <strong>2盒</strong></li></ul>');});
 main.querySelectorAll('[data-bucket]').forEach(b=>b.onclick=()=>{const k=b.dataset.bucket,rows=orders.filter(o=>k==='awaiting'?o.status==='awaiting_payment':k==='ready'?o.status==='ready_to_ship':k==='pending'?o.payment==='pending':k==='review'?o.status==='completed'&&o.commission:o.status==='shipped');showInfo(b.closest('article').querySelector('h2').textContent,rows.length?`<ul class="info-list">${rows.map(o=>`<li><a href="${href(o.id)}">${o.id} · ${o.customer}</a><span>¥${o.amount}</span></li>`).join('')}</ul>`:'<p class="muted">暂无匹配订单。</p>');});
 document.querySelector('#info').onclick=e=>{if(e.target.closest('a'))document.querySelector('#info').close();};
 main.querySelectorAll('[data-proof]').forEach(b=>b.onclick=()=>{const o=orders.find(x=>x.id===b.dataset.proof);showInfo('合成凭证上下文',`<dl class="facts"><dt>本单</dt><dd>${o.id}</dd><dt>账户</dt><dd>${o.account}</dd><dt>登记金额</dt><dd>¥${esc(o.received||o.amount)}</dd></dl><p class="small muted subsection">此原型不包含真实交易图片；实际凭证查看沿用现有受控图片入口。</p>`);});
 main.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{main.querySelectorAll('[data-day]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));const [day,value]=trendRows()[Number(b.dataset.day)];document.querySelector('#trend-value').textContent=`10月${day}日 · ¥${value.toLocaleString('en-US',{minimumFractionDigits:3,maximumFractionDigits:3})}`;drawTrend();});
 const c=main.querySelector('#trend');if(c){trendObserver=new ResizeObserver(drawTrend);trendObserver.observe(c);drawTrend();}
}
function drawTrend(){
 const c=document.querySelector('#trend');if(!c||c.getBoundingClientRect().width<1)return;const width=c.getBoundingClientRect().width,height=230,dpr=window.devicePixelRatio||1;c.width=width*dpr;c.height=height*dpr;const ctx=c.getContext('2d');ctx.scale(dpr,dpr);const css=getComputedStyle(document.documentElement),ink=css.getPropertyValue('--muted'),line=css.getPropertyValue('--line'),brand=css.getPropertyValue('--brand');const left=52,right=14,top=16,bottom=15,plotW=width-left-right,plotH=height-top-bottom;ctx.clearRect(0,0,width,height);ctx.font='12px -apple-system, sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
 for(let v=0;v<=2000;v+=500){const y=top+plotH*(1-v/2000);ctx.strokeStyle=line;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillStyle=ink;ctx.fillText(v===0?'0':v.toLocaleString('en-US'),left-10,y);}
 const points=trendRows().map(([d,v],i)=>[left+i*plotW/6,top+plotH*(1-v/2000)]);ctx.strokeStyle=brand;ctx.lineWidth=2.5;ctx.lineJoin='round';ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();const selected=Number(document.querySelector('[data-day][aria-pressed=true]')?.dataset.day??6);points.forEach(([x,y],i)=>{ctx.fillStyle=brand;ctx.beginPath();ctx.arc(x,y,i===selected?5:3,0,Math.PI*2);ctx.fill();});
}
