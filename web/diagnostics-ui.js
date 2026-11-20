// Model, prompt and evidence text is always escaped, including historical turns.
export function installDiagnostics({$,esc,state,pages,render,siteApi}){
 pages.diagnostics=async gen=>{
  const d=await siteApi('diagnostics'),active=()=>gen===state.generation;
  let selected=null,selectedRoot=null,parent=null,busy=false,composeVersion=0;
  if(!render(gen,'Ask about ring, SNG and MTT operations. Follow up within an investigation or start a new one.',`
   <div class="diagnostics-layout"><aside class="card diagnosis-history"><h2>Your requests</h2><p>Private investigations for you and this site.</p><div id="diagnosis-history"></div></aside>
   <section><div class="card diagnosis-compose"><div class="diagnosis-thread-tools"><span class="pill">${d.mode==='recorded'?'Recorded demonstration · no live data':'Live diagnosis'}</span><button type="button" class="button small" id="diagnosis-new">New investigation</button></div>
   <h2 id="diagnosis-compose-title">What would you like to investigate?</h2><p id="diagnosis-context" class="sub">A new investigation starts without conversation history.</p>
   <form id="diagnosis-form"><label for="diagnosis-prompt">Your question</label><textarea id="diagnosis-prompt" rows="4" minlength="3" maxlength="2000" required placeholder="Why are fewer ring entries running than scheduled?"></textarea>
   <div class="diagnosis-examples"><button type="button" data-example="Why are fewer ring entries running than scheduled?">Ring shortfall</button><button type="button" data-example="Are SNG bots following their schedule?">SNG health</button><button type="button" data-example="Why are bots not registering for an MTT? Check plans, lobby freshness and recent results.">MTT registration</button></div>
   <button class="button primary" id="diagnosis-submit">Investigate</button><p class="sub">Diagnosis only. The agent cannot change settings, register bots, move funds or restart workers.</p></form><p id="diagnosis-error" role="alert"></p></div>
   <div id="diagnosis-answer" aria-live="polite"></div></section></div>`))return;

  function composer(latest=null,limit=20){
   const pending=latest&&!['completed','failed'].includes(latest.status),full=latest&&latest.turn_index+1>=limit;
   parent=latest&&!pending&&!full?latest.id:null;
   $('#diagnosis-compose-title').textContent=latest?'Follow up on this investigation':'What would you like to investigate?';
   $('#diagnosis-context').textContent=pending?'Wait for this answer before asking a follow-up.':full?'This investigation has reached its question limit. Start a new investigation.':parent?'The original question and recent answers are included as bounded context. The agent can refresh live evidence. All messages remain readable in this thread.':'A new investigation starts without conversation history.';
   $('#diagnosis-submit').textContent=parent?'Ask follow-up':pending?'Investigation running…':'Investigate';
   $('#diagnosis-submit').disabled=!!pending||!!full;
   $('#diagnosis-prompt').placeholder=parent?'Ask about this diagnosis, for example: Why is that the most likely cause?':'Why are fewer ring entries running than scheduled?';
  }
  function intro(){
   $('#diagnosis-answer').innerHTML='<div class="card diagnosis-answer"><h2>Evidence before conclusions</h2><p>Ask a question or open an earlier investigation. Follow-ups keep the dialogue together.</p></div>';
  }
  function history(items){
   $('#diagnosis-history').innerHTML=items.length?items.map(r=>`<button type="button" class="diagnosis-history-item ${r.id===selectedRoot?'active':''}" data-diagnosis="${esc(r.latest_id||r.id)}"><strong>${esc(r.prompt)}</strong><small>${esc(new Date(r.created_utc).toLocaleString())} · ${esc(r.status)}${r.turn_count>1?` · ${Number(r.turn_count)} questions`:''}</small></button>`).join(''):'<p>No investigations yet.</p>';
   document.querySelectorAll('[data-diagnosis]').forEach(b=>b.onclick=()=>open(b.dataset.diagnosis));
  }
  function evidenceHtml(items){
   return items.map(e=>`<details><summary>${esc(e.id)} · ${esc(e.source)} · ${esc(e.captured_utc)}</summary><pre>${esc(JSON.stringify(e,null,2))}</pre></details>`).join('');
  }
  function turnHtml(t){
   const result=t.result,report=result?.report,context=result?.history_context;
   return `<div class="card diagnosis-answer diagnosis-turn" data-turn="${esc(t.id)}"><p class="sub diagnosis-turn-status">${esc(t.status)} · ${esc(new Date(t.created_utc).toLocaleString())}</p><span class="pill">${t.turn_index?'Follow-up '+Number(t.turn_index):'Initial question'}</span><h2>${esc(t.prompt)}</h2>${context?.previous_turns?`<p class="sub">Context included ${Number(context.included_turns)} of ${Number(context.previous_turns)} earlier turns in this investigation.</p>`:''}${t.error?`<p role="alert">${esc(t.error)}</p>`:''}${!report&&!t.error?'<p><span class="spinner"></span> Gathering evidence and preparing your diagnosis…</p>':''}${report?`<p class="diagnosis-summary">${esc(report.summary)}</p>${report.findings.map(f=>`<article><span class="pill">${esc(f.certainty)}</span><h3>${esc(f.title)}</h3><p>${esc(f.explanation)}</p><small>Evidence: ${f.evidence_ids.map(esc).join(', ')}</small></article>`).join('')}<h3>Evidence gaps and limitations</h3><ul>${report.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><details><summary>Evidence for this answer</summary><div class="diagnosis-evidence">${result.evidence_partial?`<button type="button" class="button small" data-load-evidence="${esc(t.id)}">Load evidence</button>`:evidenceHtml(result.evidence||[])}</div></details><p class="sub">${result.mode==='recorded'?'Recorded fixture. No live data or model call.':`${Number(result.usage?.total_tokens||0).toLocaleString()} model tokens used`}</p>`:''}</div>`;
  }
  function answer(r){
   if(!active())return;
   const turns=r.conversation||[r];selectedRoot=r.root_id||r.id;
   $('#diagnosis-answer').innerHTML=turns.map(turnHtml).join('');
   composer(r.conversation?turns.at(-1):null,r.max_turns||20);
   document.querySelectorAll('[data-load-evidence]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;b.textContent='Loading evidence…';
    try{const detail=await siteApi('diagnostics/'+b.dataset.loadEvidence);if(active()&&document.contains(b))b.parentElement.innerHTML=evidenceHtml(detail.result?.evidence||[]);}
    catch(e){if(active()&&document.contains(b)){b.disabled=false;b.textContent='Retry loading evidence';$('#diagnosis-error').textContent=e.message;}}
   });
  }
  async function refresh(){if(!active())return;const list=await siteApi('diagnostics');if(active())history(list.items);}
  async function open(id,scroll=false){
   if(!active())return;composeVersion++;selected=id;parent=null;clearInterval(state.poll);
   $('#diagnosis-submit').disabled=true;$('#diagnosis-context').textContent='Loading investigation…';
   let terminal=false,revision='',first=true;
   async function poll(){
    if(!active()||selected!==id||busy)return;busy=true;
    try{
     const r=await siteApi('diagnostics/'+id);if(!active()||selected!==id)return;
     const latest=(r.conversation||[r]).at(-1),nextRevision=`${r.status}:${latest.id}:${latest.status}`;
     if(nextRevision!==revision){answer(r);revision=nextRevision;}
     if(first&&scroll){$('.diagnosis-turn:last-child')?.scrollIntoView({behavior:'smooth',block:'start'});}first=false;
     terminal=['completed','failed'].includes(latest.status);
     if(terminal){clearInterval(state.poll);await refresh();}
    }catch(e){if(active()&&selected===id){$('#diagnosis-error').textContent=e.message;clearInterval(state.poll);terminal=true;composer();}}
    finally{busy=false;}
   }
   await poll();if(active()&&selected===id&&!terminal)state.poll=setInterval(poll,2000);
  }
  function fresh(){
   if(!active())return;composeVersion++;selected=null;selectedRoot=null;parent=null;clearInterval(state.poll);
   composer();$('#diagnosis-prompt').value='';$('#diagnosis-error').textContent='';intro();
   document.querySelectorAll('.diagnosis-history-item').forEach(b=>b.classList.remove('active'));
  }
  history(d.items);intro();
  $('#diagnosis-new').onclick=fresh;
  document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>{fresh();$('#diagnosis-prompt').value=b.dataset.example;$('#diagnosis-prompt').focus();});
  $('#diagnosis-form').onsubmit=async e=>{
   e.preventDefault();const button=$('#diagnosis-submit'),prompt=$('#diagnosis-prompt').value.trim(),version=composeVersion;
   const payload={prompt,request_id:crypto.randomUUID()};if(parent)payload.parent_id=parent;
   button.disabled=true;$('#diagnosis-error').textContent='';
   try{
    const r=await siteApi('diagnostics','POST',payload);if(!active())return;
    await refresh().catch(()=>{});if(!active()||version!==composeVersion)return;
    if($('#diagnosis-prompt').value.trim()===prompt)$('#diagnosis-prompt').value='';
    await open(r.id,true);
   }catch(error){if(active()&&version===composeVersion)$('#diagnosis-error').textContent=error.message;}
   finally{if(active()&&version===composeVersion)button.disabled=false;}
  };
 };
}
