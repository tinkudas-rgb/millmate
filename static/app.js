'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let state = null, locked = false, entry = {kind: 'brand'}, revision = 0, chatBusy = false;
  const clean = value => String(value ?? '').replace(/\b(forecast|model|algorithm|API|CSV|database|dataset|anomaly|KPI|regression|token)s?\b/gi, 'stock details');
  const num = value => new Intl.NumberFormat('en-IN', {maximumFractionDigits: 2}).format(Number(value) || 0);
  const node = (tag, value, cls) => {const el=document.createElement(tag);el.textContent=clean(value);if(cls)el.className=cls;return el;};
  const text = (id, value) => {$(id).textContent=clean(value);};
  function showError(id, err, fallback) {text(id,err?.friendly ? `${err.message} ${err.fix || ''}` : fallback);$(id).hidden=false;}
  function lock(on) {locked=on;['add-brand','demo-button','refresh','save-entry','confirm-demo'].forEach(id=>$(id).disabled=on);document.querySelectorAll('.stock-buttons button').forEach(b=>b.disabled=on);$('ask').disabled=on||chatBusy;}
  async function request(path, options={}) {
    const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),60000);
    try {const res=await fetch(path,{...options,signal:ctrl.signal});let data;try{data=await res.json();}catch(_){throw new Error('unreadable');}
      if(!res.ok){if(typeof data.message==='string')throw {...data,friendly:true};throw new Error('failed');}return data;
    }finally{clearTimeout(timer);}
  }
  const post = (path, body) => request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function niceDate(value) {const d=new Date(String(value).slice(0,10)+'T12:00:00');return Number.isNaN(d.getTime())?clean(value):d.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});}
  function steps(values) {return (values||[]).map(s=>node('li',s.result_summary || ({get_state:'Read your current stock and recent sales.',get_brand:'Checked the rice brand.',compare_brands:'Compared how your rice brands are moving.',project_stockout:'Checked how long the stock may last.',usage_trend:'Looked at recent stock going out.',draft_reorder:'Prepared a reorder note for you to review.'}[s.tool] || 'Checked your stock details.')));}
  function renderState(next) {
    if(!Array.isArray(next.brands)||!next.facts)throw new Error('incomplete');state=next;revision++;
    const facts=new Map((next.facts.brands||[]).map(f=>[f.brand_id,f]));
    text('stock-total',`${num(next.facts.total_stock_kg)} kg in stock · ${next.brands.length} rice ${next.brands.length===1?'brand':'brands'} · As of ${niceDate(next.facts.as_of)}`);
    $('empty-state').hidden=!!next.brands.length;
    $('brands').replaceChildren(...next.brands.map(b=>{
      const row=node('article','','stock-row'),info=node('div','','stock-info'),buttons=node('div','','stock-buttons'),f=facts.get(b.id);
      info.append(node('h3',b.name),node('p',b.rice_type,'rice-label'));
      const amount=node('p',num(b.stock_kg),'stock-value');amount.append(node('span',' kg in stock'));info.append(amount);
      let status='Add sales from at least 3 days to see how long your stock may last.',cls='stock-status';
      if(f && f.status!=='no_data') {
        const range=f.days_left_low!=null&&f.days_left_high!=null?(num(f.days_left_low)===num(f.days_left_high)?`About ${num(f.days_left_low)} days left`:`About ${num(f.days_left_low)} to ${num(f.days_left_high)} days left`):f.days_left_high==null&&f.days_left_low!=null?`At least ${num(f.days_left_low)} days left`:f.days_left!=null?`About ${num(f.days_left)} days left`:'Keep recording your sales';
        status=f.status==='running_out'?`Needs attention · ${range}`:f.status==='piling_up'?`Moving slowly · ${range}`:`Stock looks steady · ${range}`;
        if(f.status==='running_out')cls+=' urgent';if(f.status==='piling_up')cls+=' slow';
      }
      info.append(node('p',status,cls));
      for(const [kind,label] of [['in','+ Add stock'],['out','− Sold today']]) {const btn=node('button',label,kind==='in'?'secondary':'');btn.type='button';btn.setAttribute('aria-label',`${label} for ${clean(b.name)}`);btn.addEventListener('click',()=>openEntry(kind,b));buttons.append(btn);}
      row.append(info,buttons);return row;
    }));
    const notes=next.facts.data_notes||[];$('stock-notes').hidden=!notes.length;$('stock-notes-list').replaceChildren(...notes.map(v=>node('li',v)));
    const movements=next.movements||[];$('recent').hidden=!movements.length;
    $('movements').replaceChildren(...movements.slice().sort((a,b)=>b.date.localeCompare(a.date)).map(m=>{const row=node('div','','movement-row');row.append(node('div',`${next.brands.find(b=>b.id===m.brand_id)?.name || 'Rice brand'} · ${m.kind==='in'?'Received':'Sold / dispatched'} ${num(m.kg)} kg${m.note?` · ${m.note}`:''}`),node('span',niceDate(m.date)));return row;}));
  }
  function renderAdvice(data) {
    if(!data.story||!Array.isArray(data.actions))throw new Error('incomplete');
    text('story-headline',data.story.headline);text('story-summary',data.story.summary);
    $('story-claims').replaceChildren(...(data.story.claims||[]).map(c=>node('li',c.text)));
    $('watch-steps').replaceChildren(...steps(data.agent_steps));$('watch-work').hidden=!(data.agent_steps||[]).length;$('watch-work').open=false;
    $('actions').replaceChildren(...data.actions.slice(0,3).map((a,i)=>{
      const card=node('article','','action'),body=node('div','','action-body');body.append(node('h3',a.title),node('p',a.why),node('p',a.expected_effect,'effect'));
      const proof=node('details','');proof.append(node('summary','Show me the numbers'));const list=node('ul','');list.append(...(a.proof||[]).map(v=>node('li',v)));proof.append(list);body.append(proof);
      if(a.draft_text) {const draft=node('p',a.draft_text,'draft'),copy=node('button','Copy reorder note','copy secondary'),status=node('p','','copy-status');copy.type='button';status.setAttribute('role','status');copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(clean(a.draft_text));status.textContent='Copied. Review it before sharing.';}catch(_){status.textContent='Copy is not available here. Select the note above to copy it.';}});body.append(draft,copy,status);}
      card.append(node('span',i+1,'action-number'),body);return card;
    }));
    $('advice').hidden=false;text('last-check',`Last checked at ${new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}. I check again when you update your stock or choose "Check again".`);
  }
  async function watch() {
    $('advice').hidden=true;$('advice-error').hidden=true;text('last-check','Looking through your stock and recent sales...');
    try{renderAdvice(await request('/api/advice'));}catch(e){text('last-check','Stock is saved, but the advice could not be checked.');showError('advice-error',e,'I could not check your stock advice. Choose "Check again" to try once more.');}
  }
  async function refresh() {
    if(locked)return;lock(true);$('page-error').hidden=true;text('page-status','Opening your stock book...');
    try{renderState(await request('/api/state'));text('page-status','Your stock book is up to date.');await watch();}
    catch(e){showError('page-error',e,'I could not open your stock book. Check your connection, then choose "Check again".');text('page-status','The stock book could not be refreshed.');}
    finally{lock(false);}
  }
  function openEntry(kind, brand) {
    if(locked)return;entry={kind,brand};$('entry-form').reset();$('entry-error').hidden=true;
    const isBrand=kind==='brand';$('brand-fields').hidden=!isBrand;$('movement-fields').hidden=isBrand;$('brand-name').required=isBrand;$('quantity').min=isBrand?'0':'0.001';
    text('entry-title',isBrand?'Add a brand & stock':kind==='in'?'Add stock':'Record what went out');text('quantity-label',isBrand?'Stock you have now (kg)':kind==='in'?'New load received (kg)':'Sold or dispatched (kg)');text('save-entry',isBrand?'Save brand & stock':kind==='in'?'Save new load':'Save sale / dispatch');text('entry-context',isBrand?'':`${brand.name} · ${num(brand.stock_kg)} kg in stock`);$('entry-dialog').showModal();
  }
  $('add-brand').addEventListener('click',()=>openEntry('brand'));
  $('close-entry').addEventListener('click',()=>{if(!locked)$('entry-dialog').close();});
  $('entry-dialog').addEventListener('cancel',e=>{if(locked)e.preventDefault();});
  $('entry-form').addEventListener('submit',async e=>{
    e.preventDefault();if(locked)return;const kg=Number($('quantity').value),isBrand=entry.kind==='brand';
    if(!Number.isFinite(kg)||kg<0||(!isBrand&&kg<=0))return;
    if(isBrand&&!$('brand-name').value.trim()){showError('entry-error',null,'Please enter a brand name.');return;}
    if(!isBrand&&entry.kind==='out'&&kg>Number(entry.brand.stock_kg)){showError('entry-error',null,'That is more than the stock shown. Check the amount or add the missing new load first.');return;}
    const body=isBrand?{name:$('brand-name').value.trim(),rice_type:$('rice-type').value,stock_kg:kg}:{brand_id:entry.brand.id,kind:entry.kind,kg};
    if(isBrand&&$('reorder-level').value!=='')body.reorder_level_kg=Number($('reorder-level').value);
    if(!isBrand){if($('movement-date').value)body.date=$('movement-date').value;if($('movement-note').value.trim())body.note=$('movement-note').value.trim();}
    lock(true);$('entry-error').hidden=true;let saved=false;
    try {await post(isBrand?'/api/brands':'/api/movements',body);saved=true;$('entry-dialog').close();text('page-status','Saved. Updating your stock advice...');$('advice').hidden=true;renderState(await request('/api/state'));await watch();text('page-status','Saved. Your stock book is up to date.');}
    catch(err){if(saved){showError('page-error',err,'Your change was saved, but the stock book could not be refreshed. Choose "Check again". Do not save the same change again.');text('page-status','Saved. Please refresh to see the latest stock.');}else if(err.friendly){showError('entry-error',err,'');}else{showError('entry-error',null,'I could not confirm whether this was saved. Close this form and choose "Check again" before entering the same change again.');}}
    finally{lock(false);}
  });
  $('refresh').addEventListener('click',refresh);
  $('demo-button').addEventListener('click',()=>{if(!locked)$('demo-dialog').showModal();});
  $('cancel-demo').addEventListener('click',()=>$('demo-dialog').close());
  $('demo-dialog').addEventListener('cancel',e=>{if(locked)e.preventDefault();});
  $('confirm-demo').addEventListener('click',async()=>{
    if(locked)return;lock(true);$('demo-dialog').close();$('page-error').hidden=true;let saved=false;
    try{const data=await post('/api/demo',{});saved=true;renderState(data);$('chat-log').replaceChildren();text('page-status','Demo mill loaded. These are example stock records.');await watch();}
    catch(e){showError('page-error',e,saved?'The demo loaded, but its stock book could not be shown. Choose "Check again".':'I could not confirm whether the demo loaded. Choose "Check again" before trying again.');}finally{lock(false);}
  });
  $('chat-form').addEventListener('submit',async e=>{
    e.preventDefault();const question=$('question').value.trim();if(!question||locked||chatBusy)return;
    const version=revision;chatBusy=true;$('ask').disabled=true;$('chat-error').hidden=true;text('chat-status','Looking at your mill...');
    const add=(who,answer,cls)=>{const m=node('div','',`message ${cls||''}`);m.append(node('strong',who),node('div',answer));$('chat-log').append(m);return m;};
    add('You',question,'owner');$('question').value='';
    try{const reply=await post('/api/chat',{question});if(typeof reply.answer!=='string')throw new Error('incomplete');if(version!==revision){text('chat-status','Your stock changed while I was answering. Please ask again for the latest numbers.');return;}
      const m=add('MillMate',reply.answer);if(reply.agent_steps?.length){const fold=node('details','');fold.append(node('summary','How I worked this out'));const list=node('ol','');list.append(...steps(reply.agent_steps));fold.append(list);m.append(fold);}text('chat-status','');}
    catch(err){showError('chat-error',err,'I could not answer that right now. Please try again.');$('question').value=question;text('chat-status','');}
    finally{chatBusy=false;$('ask').disabled=locked;}
  });
  refresh();
})();
