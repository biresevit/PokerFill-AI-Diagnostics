import {installDiagnostics} from './diagnostics-ui.js';

// Public demo: all prompts, prepared responses and history stay in this browser.
// No request is made to the live investigation API and no model key is present.
const storageKey='pokerfill-ai-synthetic-v1';
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let turns=[];
try{turns=JSON.parse(localStorage.getItem(storageKey)||'[]');if(!Array.isArray(turns))turns=[];}catch{turns=[];}
const persist=()=>{try{localStorage.setItem(storageKey,JSON.stringify(turns));}catch{/* demo works without browser storage */}};
const evidence=(id,source,data)=>({id,source,captured_utc:'2026-11-19T19:05:00Z',
  site_id:9001,coverage:'Synthetic demonstration fixture; not production evidence',data});
const finding=(title,explanation,certainty,evidence_ids)=>({title,explanation,certainty,evidence_ids});

function sample(prompt,parent){
 const lower=prompt.toLowerCase();let report,items;
 if(lower.includes('restart')||lower.includes('worker 212')){
  items=[evidence('E1','get_worker_log_signals',{machine_id:212,collector_time_zone:'Europe/Budapest',
   events:[{timestamp_utc:'2026-11-19T19:02:02Z',code:'heartbeat_timeout',seconds_without_publish:120},
   {timestamp_utc:'2026-11-19T19:02:07Z',code:'worker_site_restart'}],coverage:'Complete sample log covers the requested incident'}),
   evidence('E2','get_incident_timeline',{deployment_completed_utc:'2026-11-19T16:30:00Z',worker_restarted_utc:'2026-11-19T19:02:07Z'})];
  report={summary:'The sample worker restarted after a database heartbeat timeout. The logs cover the incident and place the restart at 20:02 CET. The earlier deployment does not coincide with it.',
   findings:[finding('Heartbeat watchdog triggered the restart','The sample log records 120 seconds without a successful database heartbeat, followed five seconds later by a site-worker restart.','confirmed',['E1']),
   finding('Deployment timing does not match','The update completed at 17:30 CET, more than two hours before the restart. The available evidence points to the heartbeat watchdog rather than that deployment.','confirmed',['E1','E2'])],
   limitations:['The initiating reason for the database interruption is not in this sample. A timeout does not establish whether the cause was networking, SQL load or an application stall.']};
 }else if(lower.includes('balance')||lower.includes('fund')||lower.includes('unobserved')){
  items=[evidence('E1','get_investigation_history',{included_turns:parent?1:0,meaning:'Earlier dialogue is context, not current proof',previous_diagnosis:parent?.result.report.summary||null}),
   evidence('E2','get_funding_capacity',{configured_accounts:60,accounts_with_observed_wallets:42,accounts_with_unobserved_funds:18,native_units_conversion_verified:false})];
  report={summary:'Unknown funds are not the same as insufficient funds. In this sample, 18 accounts have no usable wallet observation, so a funding shortfall cannot be confirmed.',
   findings:[finding('Missing observations remain unknown','A missing balance does not mean an account has zero funds. The sample contains observations for 42 accounts and no usable observation for 18.','confirmed',['E2']),
   finding('A funding cause is unconfirmed',parent?'The earlier answer remains available as context. This wallet evidence does not establish an additional funding cause.':'No verified balance-to-buy-in comparison establishes a funding shortage in this sample.','unknown',parent?['E1','E2']:['E2'])],
   limitations:['Wallet snapshots are cached observations. Native balances cannot be compared with buy-ins without a verified unit conversion.']};
 }else if(lower.includes('mtt')||lower.includes('registering')){
  items=[evidence('E1','get_tournament_diagnostics',{tournament:'Evening Open',planned_registrations:10,confirmed_registrations:4}),
   evidence('E2','get_account_eligibility',{configured_accounts:10,currently_in_cooldown:6,current_eligible_capacity:4}),
   evidence('E3','get_funding_capacity',{unobserved_wallets:3})];
  report={summary:'The sample MTT has four confirmed registrations against a plan of ten. Six configured accounts are cooling down, which limits currently eligible capacity to four.',
   findings:[finding('Account availability limits registrations','The sample eligibility tool reports six accounts in cooldown and capacity for four registrations now. This is a supported operational constraint on the plan.','confirmed',['E1','E2']),
   finding('A funding shortage is unconfirmed','Three wallet observations are missing. Those funds are unknown and do not prove that the accounts cannot afford registration.','unknown',['E3'])],
   limitations:['This example assumes the sample eligibility checks are complete. Live diagnoses also check lobby freshness, registration windows and command outcomes.']};
 }else if(lower.includes('sng')){
  items=[evidence('E1','get_schedule_compliance',{format:'SNG',target_registrations:12,confirmed_registrations:12}),
   evidence('E2','get_tournament_diagnostics',{waiting_for_players:2,registration_failures:0,coverage:'Synthetic recent observation'})];
  report={summary:'The sample SNG schedule is being met: twelve confirmed registrations against a target of twelve. Two games are waiting for players.',
   findings:[finding('Registration target is met','SNG counts represent confirmed registration commitments; they do not mean that every game has started.','confirmed',['E1']),
   finding('Waiting games are not failed registrations','The sample lobby reports two games waiting for players and no recent registration failure in its retained window.','confirmed',['E2'])],
   limitations:['A bounded result with no failures does not prove that none occurred outside the sample window.']};
 }else{
  items=[evidence('E1','get_schedule_compliance',{format:'RING',target_entries:80,confirmed_seated_entries:52}),
   evidence('E2','get_machine_health',{active_workers:3,ready_workers:3,heartbeat_age_seconds:12}),
   evidence('E3','get_lobby_snapshot',{latest_observation_utc:'2026-11-19T18:44:00Z',captured_utc:'2026-11-19T19:05:00Z',freshness_limit_seconds:45})];
  report={summary:'The sample ring shortfall aligns with stale lobby observations. Workers are healthy, but the latest lobby snapshot is too old to support planning new joins. A funding shortage is not established.',
   findings:[finding('Lobby observations are stale','The latest sample lobby observation is 21 minutes old, beyond the configured 45-second freshness limit. New joins depend on current table availability.','confirmed',['E3']),
   finding('Workers are online, while entries remain below target','All three sample workers have fresh heartbeats. The schedule calls for 80 ring entries, with 52 confirmed seats. This does not establish a worker outage.','confirmed',['E1','E2'])],
   limitations:['The sample does not identify why lobby observation stopped. Account funding and login outcomes have not been established by these three evidence sources.']};
 }
 return {report,evidence:items,mode:'recorded',usage:{total_tokens:0},history_context:{included_turns:parent?1:0,previous_turns:parent?1:0}};
}

function addTurn(payload){
 const parent=payload.parent_id?turns.find(t=>t.id===payload.parent_id):null;
 const turn={id:payload.request_id,parent_id:parent?.id||null,root_id:parent?.root_id||payload.request_id,
  turn_index:parent?parent.turn_index+1:0,prompt:payload.prompt,status:'completed',created_utc:new Date().toISOString(),error:null,
  result:sample(payload.prompt,parent)};
 turns.push(turn);if(turns.length>60)turns=turns.slice(-40);persist();return turn;
}

async function siteApi(path,method='GET',payload){
 if(method==='POST'){const t=addTurn(payload);return {id:t.id,status:t.status};}
 if(path==='diagnostics'){
  const roots=new Map();for(const turn of turns){const list=roots.get(turn.root_id)||[];list.push(turn);roots.set(turn.root_id,list);}
  return {mode:'recorded',items:[...roots.values()].reverse().map(list=>({id:list[0].root_id,latest_id:list.at(-1).id,prompt:list[0].prompt,status:list.at(-1).status,created_utc:list.at(-1).created_utc,turn_count:list.length}))};
 }
 const t=turns.find(t=>t.id===path.split('/').at(-1));if(!t)throw new Error('Sample investigation not found.');
 return {...t,conversation:turns.filter(x=>x.root_id===t.root_id),max_turns:20};
}

if(!turns.length)addTurn({request_id:crypto.randomUUID(),prompt:'Why are fewer ring entries running than scheduled?'});
const state={generation:1},pages={};
function render(gen,description,html){if(gen!==state.generation)return false;$('#page-description').textContent=description;$('#app').innerHTML=html;return true;}
installDiagnostics({$,esc,state,pages,render,siteApi});
await pages.diagnostics(state.generation);
document.querySelector('[data-diagnosis]')?.click();
$('#sample-restart').onclick=()=>{
 $('#diagnosis-new').click();$('#diagnosis-prompt').value='Why did Worker 212 restart around 20:02 CET?';
 $('#diagnosis-form').requestSubmit();
};
