(function(){
  "use strict";
  var COMMISSION=0.07, KEY="goevent_calc_history_v1";
  var nf=new Intl.NumberFormat('fr-FR');
  var reduceMotion=window.matchMedia('(prefers-reduced-motion:reduce)').matches;
  function fFCFA(n){return nf.format(Math.round(n))+' FCFA';}
  function num(v){v=(v==null?'':(''+v)).replace(/[^\d.,-]/g,'').replace(/\s/g,'').replace(',','.');var x=parseFloat(v);return isFinite(x)?x:0;}
  function esc(x){return (''+x).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function clampPct(p){return Math.max(0,Math.min(100,p));}
  function $(id){return document.getElementById(id);}
  function hide(id){$(id).style.display='none';}
  function show(id,d){$(id).style.display=d||'block';}

  var mem=null;
  function store(v){try{localStorage.setItem(KEY,JSON.stringify(v));}catch(e){mem=v;}}
  function load(){try{var r=localStorage.getItem(KEY);return r?JSON.parse(r):[];}catch(e){return mem||[];}}

  var fixedRows=$('fixedRows'), tierRows=$('tierRows');
  var FIXED_SEED=['Location salle','Sonorisation','Sécurité'];
  var TIER_SEED=['VIP','Standard','Early bird'];

  function fixedRow(label,amount){
    var d=document.createElement('div');d.className='row fixed';
    d.innerHTML='<input class="fx-label" type="text" placeholder="Poste" value="'+esc(label||'')+'">'+
      '<input class="fx-amount" inputmode="numeric" placeholder="FCFA" value="'+esc(amount||'')+'">'+
      '<button class="del" aria-label="Retirer">×</button>';
    d.querySelector('.del').addEventListener('click',function(){d.remove();compute();});
    return d;
  }
  function tierRow(name,price,quota){
    var d=document.createElement('div');d.className='row tier';
    d.innerHTML='<input class="t-name" type="text" placeholder="Nom" value="'+esc(name||'')+'">'+
      '<input class="t-price" inputmode="numeric" placeholder="Prix" value="'+esc(price||'')+'">'+
      '<input class="t-quota" inputmode="numeric" placeholder="Places" value="'+esc(quota||'')+'">'+
      '<button class="del" aria-label="Retirer">×</button>';
    d.querySelector('.del').addEventListener('click',function(){d.remove();compute();});
    return d;
  }
  function seed(){fixedRows.innerHTML='';tierRows.innerHTML='';FIXED_SEED.forEach(function(l){fixedRows.appendChild(fixedRow(l,''));});TIER_SEED.forEach(function(n){tierRows.appendChild(tierRow(n,'',''));});}
  seed();

  $('addFixed').addEventListener('click',function(){fixedRows.appendChild(fixedRow('',''));});
  $('addTier').addEventListener('click',function(){tierRows.appendChild(tierRow('','',''));compute();});
  document.addEventListener('input',compute);
  $('boost').addEventListener('change',compute);
  $('pType').addEventListener('change',compute);
  var freeToggle=$('freeToggle');
  freeToggle.addEventListener('change',function(){
    $('freeParams').classList.toggle('hidden',!freeToggle.checked);
    tierRows.querySelectorAll('.t-price').forEach(function(i){i.disabled=freeToggle.checked;});
    $('billNote').textContent=freeToggle.checked?"Pas de prix : le quota = les places / présents attendus.":"Un palier par type de billet. Le quota = nombre de places de ce type.";
    compute();
  });

  function readForm(){
    var fixed=[];fixedRows.querySelectorAll('.row.fixed').forEach(function(r){fixed.push({label:r.querySelector('.fx-label').value,amount:r.querySelector('.fx-amount').value});});
    var tiers=[];tierRows.querySelectorAll('.row.tier').forEach(function(r){tiers.push({name:r.querySelector('.t-name').value,price:r.querySelector('.t-price').value,quota:r.querySelector('.t-quota').value});});
    return {name:$('pName').value,type:$('pType').value,fixed:fixed,contingency:$('contingency').value,varCost:$('varCost').value,tiers:tiers,boost:$('boost').value,free:freeToggle.checked,convPct:$('convPct').value,futureBasket:$('futureBasket').value,pess:$('sPess').value,real:$('sReal').value,opti:$('sOpti').value};
  }

  function model(f){
    var fixedSum=f.fixed.reduce(function(a,b){return a+num(b.amount);},0);
    var boost=num(f.boost);
    var fixedTotal=fixedSum*(1+num(f.contingency)/100)+boost;
    var tiers=f.tiers.map(function(t){return {name:(t.name||'Palier'),price:num(t.price),quota:num(t.quota)};}).filter(function(t){return t.quota>0;});
    var capacity=tiers.reduce(function(a,t){return a+t.quota;},0);
    var grossFull=f.free?0:tiers.reduce(function(a,t){return a+t.price*t.quota;},0);
    var varCost=num(f.varCost);
    var contribFull=grossFull*(1-COMMISSION)-varCost*capacity;
    return {fixedSum:fixedSum,boost:boost,fixedTotal:fixedTotal,tiers:tiers,capacity:capacity,grossFull:grossFull,varCost:varCost,contribFull:contribFull,pess:clampPct(num(f.pess)),real:clampPct(num(f.real)),opti:clampPct(num(f.opti)),convPct:num(f.convPct),futureBasket:num(f.futureBasket)};
  }

  var lastClass='';
  function setVerdict(cls,emoji,html){
    $('vEmoji').textContent=emoji;
    var V=$('verdict');V.className='verdict '+cls;V.innerHTML=html;
    $('verdictBanner').className='verdict-banner '+cls;
    if(cls!==lastClass && !reduceMotion){var b=$('verdictBanner');b.classList.remove('flash');void b.offsetWidth;b.classList.add('flash');}
    lastClass=cls;
  }

  function compute(){
    var f=readForm(), m=model(f);
    $('fixedTotalTag').textContent=m.fixedSum>0?('Total : '+fFCFA(m.fixedSum)):'';
    $('resultSub').textContent=(f.name?f.name:'Projet sans nom')+' · '+f.type;
    var haveData=m.capacity>0 && (m.fixedTotal>0 || f.free);
    hide('sensCard');hide('boostCard');hide('adviceCard');

    if(!haveData){
      setVerdict('neutral','🧮','Prêt à simuler&nbsp;?<small>Remplis les coûts et les billets à gauche : ton résultat apparaît ici en direct.</small>');
      hide('richResult');window.__res={ok:false};return;
    }
    show('richResult');

    if(f.free){ computeFree(f,m); renderAdvice(f,m,window.__res); return; }

    show('gaugeBlock');show('scenBlock');
    var beRatio, bePct, beTickets;
    if(m.contribFull<=0){
      setVerdict('bad','⚠️','Chaque billet coûte plus qu\'il ne rapporte<small>À ce prix, remplir la salle creuse le déficit. Monte le prix ou baisse le coût par participant.</small>');
      paintGauge(101,m);bePct=null;
      window.__res={ok:true,free:false,impossible:true,m:m,f:f};
    } else {
      beRatio=m.fixedTotal/m.contribFull; bePct=beRatio*100; beTickets=Math.ceil(beRatio*m.capacity);
      if(bePct>100){
        setVerdict('bad','⚠️','Même salle pleine, tu ne couvres pas tes coûts<small>Il manquerait '+fFCFA(m.fixedTotal-m.contribFull)+' à guichets fermés. Monte le prix, coupe des coûts, ou vise plus grand.</small>');
        paintGauge(100,m);
      } else {
        var netReal=scenNet(m.real,m);
        setVerdict('ok','🎉','Rentable dès '+Math.round(bePct)+'% de remplissage<small>Soit '+nf.format(beTickets)+' billet'+(beTickets>1?'s':'')+' sur '+nf.format(m.capacity)+'. Au scénario réaliste ('+m.real+'%), tu dégages '+(netReal>=0?'+':'')+fFCFA(netReal)+'.</small>');
        paintGauge(bePct,m);
      }
      window.__res={ok:true,free:false,bePct:bePct,beTickets:beTickets,m:m,f:f};
    }
    $('gSeuilBottom').textContent = (bePct==null||bePct>100)?'Seuil : hors salle':('Seuil : '+Math.round(bePct)+'%');
    renderScenarios(m);renderKeyFigs(f,m,window.__res);renderSens(m);renderBoost(m);
    renderAdvice(f,m,window.__res);buildSummary(f,m,window.__res);
  }

  function computeFree(f,m){
    hide('gaugeBlock');hide('scenBlock');
    var att=Math.round(m.capacity*m.real/100);
    var costReal=m.fixedTotal+m.varCost*att;
    var perContact=att>0?costReal/att:0;
    var need=null,exp=null,netAcq=null;
    if(m.convPct>0 && m.futureBasket>0){
      need=Math.ceil(costReal/(m.futureBasket*(1-COMMISSION)));
      exp=Math.round(att*m.convPct/100);
      netAcq=exp*m.futureBasket*(1-COMMISSION)-costReal;
      if(exp>=need) setVerdict('acq','🎯','Rentable dès '+nf.format(need)+' reconversion'+(need>1?'s':'')+'<small>À '+Math.round(m.convPct)+'% tu en attends '+nf.format(exp)+' → valeur d\'acquisition nette '+(netAcq>=0?'+':'')+fFCFA(netAcq)+'.</small>');
      else setVerdict('acq','🎯','Il te faut '+nf.format(need)+' reconversions pour rembourser<small>À '+Math.round(m.convPct)+'% tu n\'en attends que '+nf.format(exp)+'. Baisse le coût par présent, ou soigne ta relance J+30.</small>');
    } else {
      setVerdict('acq','🎯','Coût de ton audience : '+fFCFA(costReal)+'<small>'+nf.format(att)+' présents visés, soit '+fFCFA(perContact)+' par contact. Renseigne ta reconversion pour voir s\'il se rembourse.</small>');
    }
    window.__res={ok:true,free:true,att:att,costReal:costReal,perContact:perContact,need:need,exp:exp,netAcq:netAcq,m:m,f:f};
    renderKeyFigs(f,m,window.__res);
    hide('boostCard');hide('sensCard');
    buildSummary(f,m,window.__res);
  }

  function paintGauge(bePct,m){
    var red=$('gRed'),green=$('gGreen'),seuil=$('gSeuil');
    document.querySelectorAll('.scen-tick').forEach(function(n){n.remove();});
    var p=Math.max(0,Math.min(100,bePct));
    red.style.width=p+'%';green.style.width=(100-p)+'%';
    seuil.style.left=(bePct>100?100:p)+'%';
    [['pess',m.pess,'var(--coral)'],['real',m.real,'var(--gold)'],['opti',m.opti,'var(--greenlit)']].forEach(function(sc){
      var t=document.createElement('div');t.className='scen-tick';t.style.left=clampPct(sc[1])+'%';t.style.background=sc[2];t.title=sc[0];$('gauge').appendChild(t);
    });
  }

  function scenNet(fillPct,m){var fr=clampPct(fillPct)/100,gross=m.grossFull*fr,commission=gross*COMMISSION,varc=m.varCost*m.capacity*fr;return gross-commission-varc-m.fixedTotal;}

  var SCEN=[['Pessimiste','☁','var(--coral)'],['Réaliste','☀','var(--gold)'],['Optimiste','★','var(--greenlit)']];
  var prevNets={};
  function renderScenarios(m){
    var vals=[m.pess,m.real,m.opti],html='';
    SCEN.forEach(function(s,i){
      var net=scenNet(vals[i],m),cls=net>=0?'ok':'bad',t=Math.round(m.capacity*clampPct(vals[i])/100);
      html+='<div class="scen"><div class="scen-head"><div class="scen-ico" style="background:'+s[2]+'">'+s[1]+'</div>'+
        '<div class="scen-meta"><b>'+s[0]+'</b><span>'+clampPct(vals[i])+'% · '+nf.format(t)+' billets</span></div></div>'+
        '<div class="net '+cls+'" data-i="'+i+'" data-to="'+net+'">'+(net>=0?'+':'')+fFCFA(net)+'</div></div>';
    });
    $('scenRow').innerHTML=html;
    if(!reduceMotion){$('scenRow').querySelectorAll('.net').forEach(function(el){var i=el.getAttribute('data-i'),to=parseFloat(el.getAttribute('data-to')),from=prevNets[i]||0;tween(el,from,to);prevNets[i]=to;});}
  }
  function tween(el,from,to){var t=(el._tk=(el._tk||0)+1),start=null,dur=350;function step(ts){if(el._tk!==t)return;if(start===null)start=ts;var k=Math.min(1,(ts-start)/dur);var e=1-Math.pow(1-k,3);var v=from+(to-from)*e;el.textContent=(v>=0?'+':'')+fFCFA(v);if(k<1)requestAnimationFrame(step);}requestAnimationFrame(step);}

  function kf(label,val,hl){return '<div class="keyfig'+(hl?' hl':'')+'"><p class="kf-l">'+label+'</p><p class="kf-v">'+val+'</p></div>';}
  function renderKeyFigs(f,m,res){
    var h='';
    if(res.free){
      h+=kf('Coût total',fFCFA(res.costReal));
      h+=kf('Présents visés',nf.format(res.att));
      h+=kf('Coût / contact',fFCFA(res.perContact),true);
      h+=kf('Reconv. nécessaires',res.need!=null?nf.format(res.need):'—');
      h+=kf('Reconv. attendues',res.exp!=null?nf.format(res.exp):'—');
      h+=kf('Valeur acquisition',res.netAcq!=null?((res.netAcq>=0?'+':'')+fFCFA(res.netAcq)):'—');
    } else {
      var seuilTxt=res.impossible?'—':(res.bePct>100?'> 100%':Math.round(res.bePct)+'%');
      h+=kf('Coûts fixes totaux',fFCFA(m.fixedTotal));
      h+=kf('Capacité totale',nf.format(m.capacity)+' billets');
      h+=kf('Recette max',fFCFA(m.grossFull));
      h+=kf('Contribution max',fFCFA(m.contribFull));
      h+=kf('Seuil rentabilité',seuilTxt,true);
      h+=kf('Contribution / billet',m.capacity?fFCFA(m.contribFull/m.capacity):'—');
    }
    $('keyFigs').innerHTML=h;
  }

  function renderSens(m){
    var card=$('sensCard');if(!m.tiers.length){card.style.display='none';return;}
    var fr=m.real/100,step=500,lines='';
    m.tiers.forEach(function(t){
      var d=step*t.quota*fr*(1-COMMISSION);
      lines+='<div class="sens-line"><div class="sens-l"><b>'+esc(t.name)+'</b><span>'+fFCFA(t.price)+' → '+fFCFA(t.price+step)+'</span></div><div class="sens-v">+'+fFCFA(d)+'</div></div>';
    });
    $('sensBody').innerHTML=lines;card.style.display='block';
  }
  function renderBoost(m){
    var card=$('boostCard');if(!m.boost){card.style.display='none';return;}
    card.style.display='block';
    var npt=m.capacity>0?(m.grossFull/m.capacity)*(1-COMMISSION)-m.varCost:0;
    var b=$('boostBody');
    if(npt<=0){b.innerHTML='Ton Boost coûte <span class="hl">'+fFCFA(m.boost)+'</span>, mais à ce prix chaque entrée ne dégage pas de marge — impossible à rembourser en volume. Corrige d\'abord ta billetterie.';return;}
    var need=Math.ceil(m.boost/npt);
    b.innerHTML='Ton Boost coûte <span class="hl">'+fFCFA(m.boost)+'</span>. Chaque billet en plus rapporte net <span class="hl">'+fFCFA(npt)+'</span>. Il est rentable dès qu\'il t\'amène plus de <span class="hl">'+nf.format(need)+' billet'+(need>1?'s':'')+'</span> que tu n\'aurais pas vendus sans lui.';
  }

  var TYPE_TIP={
    'Concert':'Un concert se vend tard : l\'essentiel des billets part la dernière semaine. Ne casse pas tes prix trop tôt par panique.',
    'Cérémonie':'Sur une cérémonie, ta jauge est souvent connue d\'avance (les invités). Ton vrai risque est le coût, pas le remplissage.',
    'Gala':'Un gala vit du VIP et des tables sponsors. Sécurise 2-3 tables entreprises avant même d\'ouvrir la vente au public.',
    'Conférence':'Sur une conférence, les inscriptions de groupe et l\'Early bird pro pèsent lourd. Pense à un tarif groupe.',
    'Soirée':'Une soirée se remplit à la porte. Garde de la capacité en vente le soir même, et surveille la météo.',
    'Spectacle':'Un spectacle a une jauge fixe : tes seuls leviers sont le prix et le remplissage, pas la capacité.','Autre':''
  };
  function renderAdvice(f,m,res){
    if(!res||!res.ok){hide('adviceCard');return;}
    var A=[];
    if(res.free){
      if(!(m.convPct>0 && m.futureBasket>0)) A.push(['warn','Renseigne ton taux de reconversion et le panier d\'un futur billet : sans ça, impossible de dire si ton gratuit se rembourse.']);
      else{ if(res.exp>=res.need) A.push(['good','Ton hypothèse de reconversion couvre le coût. Le gratuit est un bon investissement d\'acquisition ici.']); else A.push(['bad','À '+Math.round(m.convPct)+'%, tu ne rembourses pas. Vise '+Math.ceil(res.need/Math.max(1,res.att)*100)+'% de reconversion, ou baisse ton coût par présent.']); }
      A.push(['warn','Le jour J, capture les contacts (téléphone/email) de chaque présent. Un gratuit sans données de contact ne se reconvertit jamais.']);
    } else {
      var bePct=res.impossible?999:res.bePct;
      if(res.impossible) A.push(['bad','À ce prix, chaque billet perd de l\'argent. Rien ne se règle en volume : monte le prix ou coupe le coût par participant.']);
      else if(bePct>100) A.push(['bad','Même à guichets fermés tu es en perte. Ta jauge est trop petite pour tes coûts, ou tes prix trop bas.']);
      else if(bePct>85) A.push(['warn','Seuil très haut ('+Math.round(bePct)+'%). La moindre sous-vente te met dans le rouge — garde de la marge.']);
      else if(bePct<=45) A.push(['good','Marge de sécurité confortable ('+Math.round(bePct)+'%). Tu pourrais tester un prix un peu plus haut sur ton meilleur palier.']);
      else A.push(['good','Seuil sain ('+Math.round(bePct)+'%). Objectif atteignable avec une vente correcte.']);
      if(num(f.contingency)===0) A.push(['warn','Aucune marge d\'imprévu. Sur un événement, prévois 10 à 15% : il y a toujours une dépense de dernière minute.']);
      if(m.tiers.length===1) A.push(['warn','Un seul palier plafonne tes revenus. Un Early bird crée l\'urgence, un VIP capte ceux qui paieraient plus.']);
      if(m.tiers.length>1){var maxP=m.tiers.reduce(function(a,t){return t.price>a.price?t:a;});if(maxP.quota>m.capacity*0.5) A.push(['warn','Ton palier le plus cher (« '+esc(maxP.name)+' ») fait plus de la moitié de ta jauge — le plus dur à remplir. Prévois un plan B.']);}
      if(m.varCost>0){var minP=m.tiers.reduce(function(a,t){return (t.price>0 && t.price<a.price)?t:a;},{price:Infinity});if(minP.price!==Infinity && m.varCost>minP.price*0.4) A.push(['warn','Ton coût par participant dépasse 40% de ton billet le moins cher — chaque entrée à ce tarif rapporte très peu.']);}
      if(m.boost>0){var npt=m.capacity>0?(m.grossFull/m.capacity)*(1-COMMISSION)-m.varCost:0;if(npt>0 && Math.ceil(m.boost/npt)>m.capacity*0.3) A.push(['warn','Ton Boost doit t\'amener beaucoup de billets pour se rembourser (plus de 30% de ta jauge). Mesure l\'effet réel.']);}
    }
    var tip=TYPE_TIP[f.type]; if(tip) A.push(['tip',tip]);
    if(!A.length) A.push(['good','Rien à signaler. Les chiffres tiennent.']);
    var sym={good:'✓',warn:'!',bad:'×',tip:'i'};
    $('adviceBody').innerHTML=A.slice(0,6).map(function(a,i){return '<li style="animation-delay:'+(i*0.04)+'s"><span class="badge '+a[0]+'">'+sym[a[0]]+'</span><span>'+a[1]+'</span></li>';}).join('');
    show('adviceCard');
  }

  function buildSummary(f,m,res){
    var L=['— GoEvent · '+(f.name?f.name:'Mon événement')+(f.type?' ('+f.type+')':'')+' —'];
    if(res.free){L.push('Événement GRATUIT · investissement d\'acquisition');L.push(nf.format(res.att)+' présents visés · coût '+fFCFA(res.costReal)+' ('+fFCFA(res.perContact)+'/contact)');if(res.need!=null)L.push('Se rembourse dès '+nf.format(res.need)+' reconversions payantes');}
    else if(res.impossible){L.push('⚠ Non rentable : chaque billet coûte plus qu\'il ne rapporte.');}
    else if(res.bePct>100){L.push('⚠ Non rentable même à guichets fermés. Coûts : '+fFCFA(m.fixedTotal));}
    else{L.push('Seuil : '+Math.round(res.bePct)+'% de remplissage ('+nf.format(res.beTickets)+'/'+nf.format(m.capacity)+' billets)');L.push('Coûts couverts : '+fFCFA(m.fixedTotal));}
    L.push('');L.push('Simulé sur GoEvent — goevent.africa');
    window.__summary=L.join('\n');
  }

  function chipDone(btn,label){var o=btn.textContent;btn.textContent=label;btn.classList.add('done');setTimeout(function(){btn.textContent=o;btn.classList.remove('done');},1500);}
  $('copyBtn').addEventListener('click',function(){var btn=this,txt=window.__summary||'Remplis le simulateur d\'abord.';
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(function(){chipDone(btn,'Copié ✓');}).catch(fb);else fb();
    function fb(){var ta=document.createElement('textarea');ta.value=txt;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();chipDone(btn,'Copié ✓');}});

  function verdictOf(res){
    if(!res||!res.ok) return {t:'—',c:'neutral'};
    if(res.free){if(res.m.convPct>0&&res.m.futureBasket>0){return res.exp>=res.need?{t:'ACQ OK',c:'acq'}:{t:'ACQ RISQUE',c:'bad'};}return {t:'ACQUISITION',c:'acq'};}
    if(res.impossible||res.bePct>100) return {t:'PERTE',c:'bad'};
    return {t:Math.round(res.bePct)+'% seuil',c:'ok'};
  }
  $('saveBtn').addEventListener('click',function(){
    var f=readForm(),res=window.__res;
    if(!res||!res.ok){chipDone(this,'Simule d\'abord');return;}
    var v=verdictOf(res);
    var item={id:Date.now(),name:f.name||('Projet '+f.type),type:f.type,createdAt:new Date().toISOString(),snapshot:f,vt:v.t,vc:v.c};
    var h=load();h.unshift(item);if(h.length>30)h=h.slice(0,30);store(h);renderHistory();chipDone(this,'Enregistré ✓');
  });

  function restore(f){
    $('pName').value=f.name||'';$('pType').value=f.type||'Concert';
    fixedRows.innerHTML='';(f.fixed&&f.fixed.length?f.fixed:[{label:'',amount:''}]).forEach(function(r){fixedRows.appendChild(fixedRow(r.label,r.amount));});
    tierRows.innerHTML='';(f.tiers&&f.tiers.length?f.tiers:[{name:'',price:'',quota:''}]).forEach(function(r){tierRows.appendChild(tierRow(r.name,r.price,r.quota));});
    $('contingency').value=f.contingency||'';$('varCost').value=f.varCost||'';$('boost').value=f.boost||'0';
    $('sPess').value=f.pess||'40';$('sReal').value=f.real||'65';$('sOpti').value=f.opti||'90';
    $('convPct').value=f.convPct||'';$('futureBasket').value=f.futureBasket||'';
    prevNets={};freeToggle.checked=!!f.free;freeToggle.dispatchEvent(new Event('change'));
    window.scrollTo({top:0,behavior:reduceMotion?'auto':'smooth'});
  }
  function renderHistory(){
    var h=load(),body=$('histBody');
    if(!h.length){body.innerHTML='<p class="hist-empty">Aucun projet enregistré. Simule, puis « Enregistrer ».</p>';return;}
    body.innerHTML='';
    h.forEach(function(it){
      var d=new Date(it.createdAt),ds=d.toLocaleDateString('fr-FR',{day:'2-digit',month:'short'});
      var row=document.createElement('div');row.className='hist-item';
      row.innerHTML='<div class="hist-main"><div class="nm">'+esc(it.name)+'</div><div class="mt">'+esc(it.type)+' · '+ds+'</div></div><span class="hist-v '+it.vc+'">'+esc(it.vt)+'</span><button class="hist-del" aria-label="Supprimer">×</button>';
      row.querySelector('.hist-main').addEventListener('click',function(){restore(it.snapshot);});
      row.querySelector('.hist-del').addEventListener('click',function(){var h2=load().filter(function(x){return x.id!==it.id;});store(h2);renderHistory();});
      body.appendChild(row);
    });
  }

  renderHistory();compute();

  var calcBtn=$('calcBtn');
  if(calcBtn){calcBtn.addEventListener('click',function(){
    compute();
    var r=$('result'); if(r) r.scrollIntoView({behavior:reduceMotion?'auto':'smooth',block:'start'});
    var b=$('verdictBanner'); if(b && !reduceMotion){b.classList.remove('flash');void b.offsetWidth;b.classList.add('flash');}
  });}

  // Tarifs en vigueur (back-office → Tarifs, via ../cms.js) : commission orga et forfaits Boost
  function applyPricing(p){
    if(!p) return;
    if(p.commission && typeof p.commission.organizer_pct==='number') COMMISSION=p.commission.organizer_pct/100;
    if(p.boost){
      var sel=$('boost'), cur=sel.value, html='<option value="0">Aucun</option>';
      ['7d','30d','90d'].forEach(function(code){
        var b=p.boost[code]; if(!b) return;
        html+='<option value="'+b.amount+'">'+b.days+' jours — '+fFCFA(b.amount)+'</option>';
      });
      sel.innerHTML=html;
      if(cur!=='0'){var opts=sel.options; sel.selectedIndex=Math.min(Array.prototype.findIndex.call(opts,function(o){return o.value===cur;})>=0?Array.prototype.findIndex.call(opts,function(o){return o.value===cur;}):0,opts.length-1);}
    }
    compute();
  }
  document.addEventListener('DOMContentLoaded',function(){ if(window.btPricing) window.btPricing().then(applyPricing); });
})();
