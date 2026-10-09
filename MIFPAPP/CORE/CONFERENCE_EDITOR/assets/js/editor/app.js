(function () {
  'use strict';

  const EDITOR_VERSION = '1.23.4';
  const supportsFsAccess = typeof window.showDirectoryPicker === 'function';
  const MAX_LOGS = 500;
  const IMAGE_EXTENSIONS = new Set(['png','jpg','jpeg','gif','webp','svg','ico','avif']);
  const PREVIEWABLE_EXTENSIONS = new Set(['png','jpg','jpeg','gif','webp','svg','avif']);

  const state = {
    mode: 'none', // none | fs | memory
    rootHandle: null,
    projectHandle: null,
    workspaceName: '',
    projectName: '',
    projectPrefix: '',
    projects: [],
    memoryFiles: new Map(),
    memoryOverrides: new Map(),
    memoryDeleted: new Set(),
    yamlText: '',
    config: null,
    regformYamlText: '',
    regformConfig: null,
    regformDirty: false,
    people: { headers: [], rows: [], dirty: false, path: 'data/people.csv' },
    peopleDemo: { active:false, snapshot:null },
    program: { headers: [], rows: [], dirty: false, path: 'data/program.csv' },
    yamlDirty: false,
    assets: [],
    objectUrls: [],
    logs: [],
    activeView: 'overview',
    checkCursor: 0,
    activeSettingsSection: '',
    activeSettingsPage: 'home',
    sectionYamlDrafts: new Map(),
    version: { schema: 1, version: '1.0.0', status: 'draft', updated_at: '', history: [] },
    versionDirty: false,
    sectionYamlDirty: false,
    pendingFaceRow: null,
    documentSelections: { badges: new Set(), certificates: new Set() },
    documentOptions: { badges: { widthMm:74, heightMm:105, marginMm:0, gapMm:0, cutLines:true, pageOrientation:'auto', preset:'74x105', blankCount:0, previewPage:1, exportScope:'all' }, certificates: { includePresentation:true } },
    documentPreviewPerson: { badges:null, certificates:null },
    documentPreviewToken: { badges:0, certificates:0 },
    sheetObservers: { people:null, program:null },
    totem: { resolve:null, mode:'confirm' },
    previewWindow: null,
    previewUrls: [],
    globalSearchSelection: -1,
    globalSearchResults: [],
    mifpExportPreviewToken: 0
  };

  const $ = (id) => document.getElementById(id);
  const els = {};

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    Object.assign(els, {
      saveState: $('saveState'),
      saveAllBtn: $('saveAllBtn'),
      globalSearch: $('globalSearch'),
      globalSearchInput: $('globalSearchInput'),
      globalSearchResults: $('globalSearchResults'),
      exportZipBtn: $('exportZipBtn'),
      exportMifpZipTopBtn: $('exportMifpZipTopBtn'),
      refreshMifpExportBtn: $('refreshMifpExportBtn'),
      exportMifpJsonlBtn: $('exportMifpJsonlBtn'),
      exportMifpZipBtn: $('exportMifpZipBtn'),
      mifpExportEditor: $('mifpExportEditor'),
      workspaceName: $('workspaceName'),
      projectName: $('projectName'),
      openWorkspaceBtn: $('openWorkspaceBtn'),
      folderFallbackInput: $('folderFallbackInput'),
      importZipBtn: $('importZipBtn'),
      projectZipInput: $('projectZipInput'),
      projectPickerWrap: $('projectPickerWrap'),
      projectPicker: $('projectPicker'),
      newConferenceBtn: $('newConferenceBtn'),
      welcomeOpenBtn: $('welcomeOpenBtn'),
      welcomeImportZipBtn: $('welcomeImportZipBtn'),
      welcomeNewBtn: $('welcomeNewBtn'),
      welcomePanel: $('welcomePanel'),
      overviewContent: $('overviewContent'),
      overviewCards: $('overviewCards'),
      overviewFiles: $('overviewFiles'),
      openSiteBtn: $('openSiteBtn'),
      previewSiteBtn: $('previewSiteBtn'),
      releaseVersionLabel: $('releaseVersionLabel'),
      releaseStatus: $('releaseStatus'),
      releaseVersion: $('releaseVersion'),
      releaseNote: $('releaseNote'),
      bumpPatchBtn: $('bumpPatchBtn'),
      bumpMinorBtn: $('bumpMinorBtn'),
      bumpMajorBtn: $('bumpMajorBtn'),
      saveVersionBtn: $('saveVersionBtn'),
      versionHistory: $('versionHistory'),
      settingsEmpty: $('settingsEmpty'),
      settingsWorkbench: $('settingsWorkbench'),
      settingsForm: $('settingsForm'),
      settingsSectionNav: $('settingsSectionNav'),
      settingsSectionSearch: $('settingsSectionSearch'),
      settingsSectionHead: $('settingsSectionHead'),
      sectionYamlEditor: $('sectionYamlEditor'),
      sectionYamlStatus: $('sectionYamlStatus'),
      applySectionYamlBtn: $('applySectionYamlBtn'),
      saveYamlFromSettingsBtn: $('saveYamlFromSettingsBtn'),
      regformEmpty: $('regformEmpty'),
      regformContent: $('regformContent'),
      regformSectionHead: $('regformSectionHead'),
      regformForm: $('regformForm'),
      saveRegformBtn: $('saveRegformBtn'),
      assetsEmpty: $('assetsEmpty'),
      assetsContent: $('assetsContent'),
      refreshAssetsBtn: $('refreshAssetsBtn'),
      cleanUnusedAssetsBtn: $('cleanUnusedAssetsBtn'),
      assetSearch: $('assetSearch'),
      assetTargetPath: $('assetTargetPath'),
      assetChooseBtn: $('assetChooseBtn'),
      assetFileInput: $('assetFileInput'),
      assetGrid: $('assetGrid'),
      peopleEditor: $('peopleEditor'),
      programEditor: $('programEditor'),
      datesEditor: $('datesEditor'),
      checksEditor: $('checksEditor'),
      refreshChecksBtn: $('refreshChecksBtn'),
      saveDatesBtn: $('saveDatesBtn'),
      documentsEditor: $('documentsEditor'),
      savePeopleBtn: $('savePeopleBtn'),
      saveProgramBtn: $('saveProgramBtn'),
      yamlEmpty: $('yamlEmpty'),
      yamlEditorWrap: $('yamlEditorWrap'),
      yamlEditor: $('yamlEditor'),
      yamlStatus: $('yamlStatus'),
      validateYamlBtn: $('validateYamlBtn'),
      downloadYamlBtn: $('downloadYamlBtn'),
      saveYamlBtn: $('saveYamlBtn'),
      clearLogsBtn: $('clearLogsBtn'),
      logList: $('logList'),
      toastHost: $('toastHost'),
      modalBackdrop: $('modalBackdrop'),
      modalTitle: $('modalTitle'),
      modalBody: $('modalBody'),
      modalClose: $('modalClose'),
      totemBackdrop: $('totemBackdrop'),
      totemEyebrow: $('totemEyebrow'),
      totemTitle: $('totemTitle'),
      totemMessage: $('totemMessage'),
      totemInputWrap: $('totemInputWrap'),
      totemInput: $('totemInput'),
      totemCancelBtn: $('totemCancelBtn'),
      totemConfirmBtn: $('totemConfirmBtn')
    });

    bindEvents();
    renderNoProjectSheets();
    updateSaveState();
    log('info', 'editor.ready', { editorVersion: EDITOR_VERSION, fsAccess: supportsFsAccess, protocol: location.protocol });
    if (!supportsFsAccess) {
      log('warn', 'filesystem.direct_write_unavailable', { fallback: 'folder/ZIP import + downloads' });
    }
  }

  function bindEvents() {
    document.querySelectorAll('.nav-item').forEach((button) => {
      button.addEventListener('click', () => switchView(button.dataset.view));
    });

    els.openWorkspaceBtn.addEventListener('click', openWorkspace);
    $('openFolderCopyBtn').addEventListener('click', () => els.folderFallbackInput.click());
    els.welcomeOpenBtn.addEventListener('click', openWorkspace);
    els.folderFallbackInput.addEventListener('change', importFolderFallback);
    els.importZipBtn.addEventListener('click', () => els.projectZipInput.click());
    els.welcomeImportZipBtn.addEventListener('click', () => els.projectZipInput.click());
    els.projectZipInput.addEventListener('change', importConferenceZip);
    els.projectPicker.addEventListener('change', () => selectProjectByName(els.projectPicker.value));
    els.newConferenceBtn.addEventListener('click', createConferenceFromTemplate);
    els.welcomeNewBtn.addEventListener('click', createConferenceFromTemplate);
    els.saveAllBtn.addEventListener('click', saveAll);
    els.exportZipBtn.addEventListener('click', exportProjectZip);
    if (els.exportMifpZipTopBtn) els.exportMifpZipTopBtn.addEventListener('click', exportMifpZip);
    if (els.refreshMifpExportBtn) els.refreshMifpExportBtn.addEventListener('click', renderMifpExport);
    if (els.exportMifpJsonlBtn) els.exportMifpJsonlBtn.addEventListener('click', exportMifpJsonl);
    if (els.exportMifpZipBtn) els.exportMifpZipBtn.addEventListener('click', exportMifpZip);
    els.saveYamlFromSettingsBtn.addEventListener('click', saveYaml);
    if (els.saveRegformBtn) els.saveRegformBtn.addEventListener('click', saveRegformSettings);
    els.savePeopleBtn.addEventListener('click', () => saveSheet('people'));
    els.saveProgramBtn.addEventListener('click', () => saveSheet('program'));
    els.saveDatesBtn.addEventListener('click', saveImportantDates);
    if (els.refreshChecksBtn) els.refreshChecksBtn.addEventListener('click', renderContentChecks);
    els.validateYamlBtn.addEventListener('click', validateYamlFromEditor);
    els.saveYamlBtn.addEventListener('click', saveYamlFromEditor);
    els.downloadYamlBtn.addEventListener('click', () => downloadText('conference.yaml', els.yamlEditor.value || state.yamlText, 'text/yaml;charset=utf-8'));
    els.yamlEditor.addEventListener('input', () => {
      state.yamlText = els.yamlEditor.value;
      state.yamlDirty = true;
      setYamlStatus('neutral', 'YAML modified · validation required');
      updateSaveState();
    });
    els.refreshAssetsBtn.addEventListener('click', loadAssets);
    if (els.cleanUnusedAssetsBtn) els.cleanUnusedAssetsBtn.addEventListener('click', cleanUnusedAssets);
    els.assetSearch.addEventListener('input', renderAssets);
    els.assetChooseBtn.addEventListener('click', () => els.assetFileInput.click());
    els.assetFileInput.addEventListener('change', handleAddOrReplaceAsset);
    els.openSiteBtn.addEventListener('click', () => openConferencePreview('index.html'));
    els.previewSiteBtn.addEventListener('click', () => openConferencePreview('index.html'));
    els.settingsSectionSearch.addEventListener('input', renderSettingsSectionNav);
    if (els.globalSearchInput) {
      els.globalSearchInput.addEventListener('input', renderGlobalSearchResults);
      els.globalSearchInput.addEventListener('focus', renderGlobalSearchResults);
      els.globalSearchInput.addEventListener('keydown', handleGlobalSearchKeydown);
    }
    if (els.sectionYamlEditor) els.sectionYamlEditor.addEventListener('input', () => {
      state.sectionYamlDirty = true;
      setSectionYamlStatus('neutral', 'Section YAML modified · apply to synchronize');
      updateSaveState();
    });
    if (els.applySectionYamlBtn) els.applySectionYamlBtn.addEventListener('click', applySectionYamlFromEditor);
    els.bumpPatchBtn.addEventListener('click', () => bumpVersion('patch'));
    els.bumpMinorBtn.addEventListener('click', () => bumpVersion('minor'));
    els.bumpMajorBtn.addEventListener('click', () => bumpVersion('major'));
    els.saveVersionBtn.addEventListener('click', saveVersionMetadata);
    els.releaseVersion.addEventListener('input', markVersionFormDirty);
    els.releaseStatus.addEventListener('change', markVersionFormDirty);
    els.clearLogsBtn.addEventListener('click', () => { state.logs = []; renderLogs(); });
    els.modalClose.addEventListener('click', closeModal);
    els.modalBackdrop.addEventListener('click', (event) => { if (event.target === els.modalBackdrop) closeModal(); });
    els.totemCancelBtn.addEventListener('click', () => resolveTotem(null));
    els.totemConfirmBtn.addEventListener('click', confirmTotem);
    els.totemBackdrop.addEventListener('click', (event) => { if (event.target === els.totemBackdrop) resolveTotem(null); });
    els.totemInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); confirmTotem(); } });

    document.addEventListener('keydown', (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        if (els.globalSearchInput) { els.globalSearchInput.focus(); els.globalSearchInput.select(); renderGlobalSearchResults(); }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (state.config) saveAll();
      }
      if (event.key === 'Escape' && els.globalSearchResults && !els.globalSearchResults.classList.contains('hidden')) { closeGlobalSearch(); return; }
      if (event.key === 'Escape' && !els.totemBackdrop.classList.contains('hidden')) { resolveTotem(null); return; }
      if (event.key === 'Escape' && !els.modalBackdrop.classList.contains('hidden')) closeModal();
    });

    document.addEventListener('pointerdown', (event) => {
      if (els.globalSearch && !els.globalSearch.contains(event.target)) closeGlobalSearch();
    });

    window.addEventListener('beforeunload', (event) => {
      if (isDirty()) {
        event.preventDefault();
        event.returnValue = '';
      }
    });

    window.addEventListener('message', (event) => {
      const data = event && event.data;
      if (!data || data.type !== 'mifp-preview-open-page') return;
      if (!state.previewWindow || event.source !== state.previewWindow || event.origin !== location.origin) return;
      if (data.project !== state.projectName) return;
      openConferencePreview(String(data.page || 'index.html'), { reuse:true });
    });
  }

  function normalizeSearchText(value) {
    return String(value == null ? '' : value).toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[_./-]+/g,' ').replace(/\s+/g,' ').trim();
  }

  function preferredSettingsPageForSection(section) {
    const direct = SETTINGS_PAGES.find((page)=>page.id === section && settingsPageAvailable(page));
    if (direct) return direct;
    const aliases = { social_program:'social', people:'speakers', committee:'speakers' };
    if (aliases[section]) {
      const aliased = SETTINGS_PAGES.find((page)=>page.id === aliases[section] && settingsPageAvailable(page));
      if (aliased) return aliased;
    }
    const global = SETTINGS_PAGES.find((page)=>page.id === 'global' && settingsPageSections(page).includes(section));
    if (global) return global;
    return SETTINGS_PAGES.find((page)=>settingsPageSections(page).includes(section)) || null;
  }

  function globalSearchEntry(title, location, keywords, route, meta) {
    const text = [title,location,keywords,meta].filter(Boolean).join(' ');
    return { title:String(title||''), location:String(location||''), meta:String(meta||''), route, haystack:normalizeSearchText(text) };
  }

  function buildGlobalSearchIndex() {
    const entries = [];
    [
      ['Overview','Overview','workspace files release version publish',{view:'overview'}],
      ['Content Editor','Content Editor','conference yaml website pages sections settings',{view:'settings',page:'home'}],
      ['Regform','Regform','registration form php email sender recipient reply to confirmation control organizer settings yaml',{view:'regform'},'regform/settings.yaml'],
      ['Assets','Assets','images photos logos documents replace upload files gallery',{view:'assets'}],
      ['Important Dates','Important Dates','dates deadline deadlines countdown submission registration',{view:'dates'}],
      ['Content Check','Content Check','check warnings placeholders tbc tbd validation missing content',{view:'checks'}],
      ['People','People','participants speakers committee roles import export excel csv',{view:'people'}],
      ['Program','Program','schedule sessions talks program import export excel csv',{view:'program'}],
      ['Badges & Certificates','Badges & Certificates','badge badges certificate certificates signatures logo stamp export pdf docx',{view:'documents'}],
      ['Raw YAML','Raw YAML','conference yaml source advanced config',{view:'yaml'}],
      ['Logs','Logs','log debug errors warnings console',{view:'logs'}]
    ].forEach((item)=>entries.push(globalSearchEntry(item[0],item[1],item[2],item[3],item[4]||'')));

    if (!state.config) return entries;

    (state.people.rows || []).forEach((row,index)=>{
      const first=String(row['First Name']||'').trim(), last=String(row['Last Name']||'').trim();
      const title=(first+' '+last).trim() || ('Person '+(index+1));
      const values=(state.people.headers||[]).map((header)=>String(row[header]||'')).join(' ');
      entries.push(globalSearchEntry(title,'People',values+' participant speaker committee role affiliation country',{view:'people',kind:'people',row:index},'Row '+(index+1)));
    });
    (state.program.rows || []).forEach((row,index)=>{
      const title=String(row.Title||row.title||row.Description||row.description||'').trim() || ('Program row '+(index+1));
      const values=(state.program.headers||[]).map((header)=>String(row[header]||'')).join(' ');
      entries.push(globalSearchEntry(title,'Program',values+' schedule session talk speaker chair location',{view:'program',kind:'program',row:index},'Row '+(index+1)));
    });
    (state.assets||[]).forEach((asset)=>entries.push(globalSearchEntry(asset.name||fileNameFromPath(asset.path),'Assets',asset.path+' '+extensionOf(asset.path)+' image file logo photo document',{view:'assets',assetPath:asset.path},asset.path)));
    const dateItems=getConfig('important_dates.items',[]);
    if(Array.isArray(dateItems)) dateItems.forEach((item,index)=>{
      const label=String(item&&item.description||'Important date').trim()||'Important date';
      entries.push(globalSearchEntry(label,'Important Dates',[item&&item.date,item&&item.description,item&&item.note,'deadline date countdown'].filter(Boolean).join(' '),{view:'dates',dateIndex:index},String(item&&item.date||'')));
    });

    const visitedSections = new Set();
    Object.keys(state.config).forEach((section)=>{
      if (section === 'registration' && state.config.registration && typeof state.config.registration === 'object') {
        // The private form is indexed separately below.
      }
      const page = preferredSettingsPageForSection(section);
      if (!page || page.virtual === 'regform') return;
      visitedSections.add(section);
      const sectionLabel = humanizeKey(section);
      entries.push(globalSearchEntry(sectionLabel, page.label, section+' '+sectionLabel+' '+page.file, {view:'settings',page:page.id,section}, section));

      const walk = (value,path) => {
        if (section === 'registration' && path.length >= 2 && path[1] === 'form') return;
        if (Array.isArray(value)) { value.forEach((item,index)=>walk(item,path.concat(index))); return; }
        if (value && typeof value === 'object') { Object.keys(value).forEach((key)=>walk(value[key],path.concat(key))); return; }
        const leaf = String(path[path.length-1] == null ? section : path[path.length-1]);
        const pathText = displayConfigPath(path);
        const valueText = typeof value === 'string' || typeof value === 'number' ? String(value) : (typeof value === 'boolean' ? (value ? 'enabled true on' : 'disabled false off') : '');
        entries.push(globalSearchEntry(humanizeKey(leaf), page.label+' · '+sectionLabel, pathText+' '+valueText, {view:'settings',page:page.id,section,path:path.map(String)}, pathText));
      };
      walk(state.config[section],[section]);
    });

    if (state.regformConfig && typeof state.regformConfig === 'object') {
      entries.push(globalSearchEntry('Form availability','Regform','enabled submit enabled open closed availability',{view:'regform',path:['registration','form','enabled']},'regform.enabled'));
      const walkRegform = (value,path) => {
        if (Array.isArray(value)) { value.forEach((item,index)=>walkRegform(item,path.concat(index))); return; }
        if (value && typeof value === 'object') { Object.keys(value).forEach((key)=>walkRegform(value[key],path.concat(key))); return; }
        const leaf=String(path[path.length-1]||'regform');
        const valueText=typeof value === 'string' || typeof value === 'number' ? String(value) : (typeof value === 'boolean' ? (value?'enabled true on':'disabled false off') : '');
        const displayPath='regform.'+path.slice(2).join('.');
        entries.push(globalSearchEntry(humanizeKey(leaf),'Regform',displayPath+' '+valueText+' mail email sender reply recipient organizer confirmation backend upload rate limit storage',{view:'regform',path:path.map(String)},displayPath));
      };
      walkRegform(state.regformConfig,['registration','form']);
    }
    return entries;
  }

  function scoreGlobalSearchEntry(entry, query) {
    const q=normalizeSearchText(query); if(!q) return 0;
    const title=normalizeSearchText(entry.title), location=normalizeSearchText(entry.location), meta=normalizeSearchText(entry.meta);
    const terms=q.split(' ').filter(Boolean);
    if (!terms.every((term)=>entry.haystack.includes(term))) return -1;
    let score=20;
    if(title===q) score+=140; else if(title.startsWith(q)) score+=95; else if(title.includes(q)) score+=70;
    if(location===q) score+=50; else if(location.includes(q)) score+=25;
    if(meta===q) score+=45; else if(meta.includes(q)) score+=20;
    score += terms.reduce((sum,term)=>sum+(title.startsWith(term)?12:title.includes(term)?6:0),0);
    return score;
  }

  function renderGlobalSearchResults() {
    if (!els.globalSearchInput || !els.globalSearchResults) return;
    const query=String(els.globalSearchInput.value||'').trim();
    if (!query) { closeGlobalSearch(false); return; }
    const matches=buildGlobalSearchIndex().map((entry)=>({entry,score:scoreGlobalSearchEntry(entry,query)})).filter((item)=>item.score>=0).sort((a,b)=>b.score-a.score || a.entry.title.localeCompare(b.entry.title)).slice(0,14).map((item)=>item.entry);
    state.globalSearchResults=matches; state.globalSearchSelection=matches.length?0:-1;
    els.globalSearchResults.replaceChildren();
    if (!matches.length) {
      const empty=div('global-search-empty'); empty.textContent=state.config?'No matching page, section or setting.':'No match. Open a conference to search its settings.'; els.globalSearchResults.append(empty);
    } else {
      matches.forEach((entry,index)=>{
        const row=button('','global-search-result'+(index===state.globalSearchSelection?' active':'')); row.type='button'; row.setAttribute('role','option'); row.setAttribute('aria-selected',index===state.globalSearchSelection?'true':'false');
        const main=div('global-search-result-main'); const title=div('global-search-result-title',entry.title); const location=div('global-search-result-location',entry.location); main.append(title,location); row.append(main);
        if(entry.meta) row.append(div('global-search-result-meta',entry.meta));
        row.addEventListener('pointerenter',()=>{state.globalSearchSelection=index;updateGlobalSearchSelection();});
        row.addEventListener('click',()=>openGlobalSearchResult(entry));
        els.globalSearchResults.append(row);
      });
    }
    els.globalSearchResults.classList.remove('hidden'); els.globalSearchInput.setAttribute('aria-expanded','true');
  }

  function updateGlobalSearchSelection() {
    if (!els.globalSearchResults) return;
    Array.from(els.globalSearchResults.querySelectorAll('.global-search-result')).forEach((row,index)=>{const active=index===state.globalSearchSelection;row.classList.toggle('active',active);row.setAttribute('aria-selected',active?'true':'false');if(active)row.scrollIntoView({block:'nearest'});});
  }

  function handleGlobalSearchKeydown(event) {
    if (!state.globalSearchResults.length && (event.key==='ArrowDown'||event.key==='ArrowUp'||event.key==='Enter')) renderGlobalSearchResults();
    if (event.key === 'ArrowDown') { event.preventDefault(); if(state.globalSearchResults.length){state.globalSearchSelection=(state.globalSearchSelection+1)%state.globalSearchResults.length;updateGlobalSearchSelection();} return; }
    if (event.key === 'ArrowUp') { event.preventDefault(); if(state.globalSearchResults.length){state.globalSearchSelection=(state.globalSearchSelection-1+state.globalSearchResults.length)%state.globalSearchResults.length;updateGlobalSearchSelection();} return; }
    if (event.key === 'Enter') { const entry=state.globalSearchResults[state.globalSearchSelection]; if(entry){event.preventDefault();openGlobalSearchResult(entry);} return; }
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeGlobalSearch(); }
  }

  function closeGlobalSearch(blur) {
    if (els.globalSearchResults) els.globalSearchResults.classList.add('hidden');
    if (els.globalSearchInput) els.globalSearchInput.setAttribute('aria-expanded','false');
    state.globalSearchSelection=-1; state.globalSearchResults=[];
    if (blur !== false && els.globalSearchInput && document.activeElement===els.globalSearchInput) els.globalSearchInput.blur();
  }

  function elementForConfigPath(path) {
    const key=(path||[]).map(String).join('.');
    return Array.from(document.querySelectorAll('[data-config-path]')).find((node)=>node.dataset.configPath===key) || null;
  }

  function revealSearchTarget(node) {
    if (!node) return false;
    let parent=node.parentElement;
    while(parent){ if(parent.tagName==='DETAILS') parent.open=true; parent=parent.parentElement; }
    const focusable=node.matches('input,select,textarea,button')?node:node.querySelector('input,select,textarea,button');
    pulseElement(node);
    if(focusable) window.setTimeout(()=>{try{focusable.focus({preventScroll:true});}catch(_){focusable.focus();}},260);
    return true;
  }

  function openGlobalSearchResult(entry) {
    if (!entry || !entry.route) return;
    const route=entry.route; closeGlobalSearch(); if(els.globalSearchInput) els.globalSearchInput.value='';
    if (route.view !== 'settings') {
      switchView(route.view);
      if(route.view==='regform') {
        renderRegformPage();
        window.setTimeout(()=>{const target=route.path?elementForConfigPath(route.path):(document.getElementById('editor-section-regform-mail')||document.getElementById('editor-section-regform'));revealSearchTarget(target||els.regformContent);},50);
        return;
      }
      if(route.view==='people'||route.view==='program') {
        renderSpreadsheet(route.view);
        window.setTimeout(()=>{
          const host=route.view==='people'?els.peopleEditor:els.programEditor;
          const target=Array.from(host.querySelectorAll('[data-kind][data-row]')).find((node)=>node.dataset.kind===route.view&&Number(node.dataset.row)===Number(route.row));
          revealSearchTarget(target||host.querySelector('.sheet-scroll'));
        },40);
        return;
      }
      if(route.view==='assets'&&route.assetPath) {
        els.assetSearch.value=route.assetPath; renderAssets();
        window.setTimeout(()=>{const target=Array.from(els.assetGrid.querySelectorAll('[data-asset-path]')).find((node)=>node.dataset.assetPath===route.assetPath);revealSearchTarget(target||els.assetGrid);},40);
        return;
      }
      if(route.view==='dates'&&Number.isInteger(route.dateIndex)) {
        window.setTimeout(()=>{const target=Array.from(els.datesEditor.querySelectorAll('[data-date-index]')).find((node)=>Number(node.dataset.dateIndex)===route.dateIndex);revealSearchTarget(target||els.datesEditor);},40);
        return;
      }
      window.setTimeout(()=>{const panel=document.querySelector('[data-view-panel="'+route.view+'"]'); if(panel)panel.scrollTop=0;},20); return;
    }
    if (!state.config) { switchView('settings'); return; }
    if(route.page) state.activeSettingsPage=route.page;
    if(route.section) state.activeSettingsSection=route.section;
    switchView('settings'); renderSettings();
    window.setTimeout(()=>{
      let target=route.path?elementForConfigPath(route.path):null;
      if(!target && route.section) target=document.getElementById('editor-section-'+route.section);
      if(!target && route.page==='regform') target=document.getElementById('editor-section-regform-mail')||document.getElementById('editor-section-regform');
      if(target) revealSearchTarget(target);
    },50);
  }

  function switchView(view) {
    state.activeView = view;
    document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
    document.querySelectorAll('[data-view-panel]').forEach((panel) => panel.classList.toggle('active', panel.dataset.viewPanel === view));
    if (view === 'logs') renderLogs();
    if (view === 'dates' && state.config) renderImportantDates();
    if (view === 'checks' && state.config) renderContentChecks();
    if (view === 'mifp-export') renderMifpExport();
    if (view === 'documents' && state.config) renderDocuments();
    if (view === 'regform' && state.config) renderRegformPage();
  }

  function log(level, event, details) {
    const time = new Date();
    const entry = {
      time: time.toLocaleTimeString([], { hour12: false }),
      level: ['debug', 'info', 'warn', 'error'].includes(level) ? level : 'info',
      event: String(event || 'event'),
      details: details ? (window.MIFPLog?.redact(details) || '[Diagnostics unavailable]') : null
    };
    state.logs.unshift(entry);
    if (state.logs.length > MAX_LOGS) state.logs.length = MAX_LOGS;
    const eventName = entry.event.startsWith('editor.') ? entry.event : 'editor.' + entry.event;
    window.MIFPLog?.[entry.level](eventName, entry.details);
    if (state.activeView === 'logs') renderLogs();
  }

  function renderLogs() {
    els.logList.replaceChildren();
    if (!state.logs.length) {
      const empty = div('sheet-empty', 'No editor events yet.');
      els.logList.append(empty);
      return;
    }
    state.logs.forEach((entry) => {
      const row = div('log-entry ' + entry.level);
      row.append(div('log-time', entry.time), div('log-level', entry.level));
      const msg = div('log-message');
      msg.textContent = entry.event + (entry.details ? ' · ' + safeJson(entry.details) : '');
      row.append(msg);
      els.logList.append(row);
    });
  }

  function safeJson(value) {
    try { return JSON.stringify(value); } catch (_) { return String(value); }
  }

  function toast(message, kind) {
    const node = div('toast ' + (kind || ''), String(message));
    els.toastHost.append(node);
    window.setTimeout(() => node.remove(), 4200);
  }

  function showTotem(options) {
    const cfg=Object.assign({eyebrow:'MIFP Editor',title:'Confirm action',message:'',confirmLabel:'Continue',cancelLabel:'Cancel',danger:false,input:false,inputLabel:'Value',inputValue:'',inputPlaceholder:''},options||{});
    if(state.totem.resolve) resolveTotem(null);
    els.totemEyebrow.textContent=cfg.eyebrow;
    els.totemTitle.textContent=cfg.title;
    els.totemMessage.textContent=cfg.message;
    els.totemConfirmBtn.textContent=cfg.confirmLabel;
    els.totemCancelBtn.textContent=cfg.cancelLabel;
    els.totemConfirmBtn.className='button primary'+(cfg.danger?' danger':'');
    els.totemInputWrap.classList.toggle('hidden',!cfg.input);
    els.totemInputWrap.firstChild.textContent=cfg.inputLabel;
    els.totemInput.value=cfg.inputValue||'';
    els.totemInput.placeholder=cfg.inputPlaceholder||'';
    state.totem.mode=cfg.input?'prompt':'confirm';
    els.totemBackdrop.classList.remove('hidden');
    return new Promise((resolve)=>{
      state.totem.resolve=resolve;
      requestAnimationFrame(()=>{(cfg.input?els.totemInput:els.totemConfirmBtn).focus();if(cfg.input)els.totemInput.select();});
    });
  }

  function resolveTotem(value) {
    const resolve=state.totem.resolve;
    state.totem.resolve=null;
    els.totemBackdrop.classList.add('hidden');
    if(resolve) resolve(value);
  }

  function confirmTotem() {
    resolveTotem(state.totem.mode==='prompt'?els.totemInput.value:true);
  }

  function totemConfirm(title,message,confirmLabel,options) {
    return showTotem(Object.assign({title,message,confirmLabel:confirmLabel||'Continue'},options||{}));
  }

  function totemPrompt(title,message,value,options) {
    return showTotem(Object.assign({title,message,input:true,inputValue:value||'',confirmLabel:'Create'},options||{}));
  }

  function realConferenceProjects(projects) {
    return (projects || []).filter((project) => String(project.name || '').toUpperCase() !== 'TEMPLATE');
  }

  function rootCanHostConferences() {
    if (state.mode !== 'fs' || !state.rootHandle) return false;
    const real = realConferenceProjects(state.projects);
    return !(real.length === 1 && real[0].direct === true);
  }

  async function openWorkspace() {
    if (!supportsFsAccess) {
      // Same user action, older-browser implementation: the user still chooses
      // an existing conference folder; there is no separate “Import” workflow.
      toast('Choose the existing conference folder to open.', 'success');
      els.folderFallbackInput.click();
      return;
    }
    try {
      log('info', 'workspace.picker_opening', { mode: 'readwrite' });
      const handle = await window.showDirectoryPicker({ mode: 'readwrite', id: 'mifp-open-conference' });
      log('info', 'workspace.folder_selected', { name: handle.name });
      const projects = await discoverFsProjects(handle);
      const real = realConferenceProjects(projects);
      if (!real.length) throw new Error('No conference.yaml found in the selected folder or its immediate subfolders. Select the conference folder, or use New conference.');
      state.mode = 'fs';
      state.rootHandle = handle;
      state.workspaceName = handle.name;
      state.memoryFiles.clear();
      state.memoryOverrides.clear();
      state.memoryDeleted.clear();
      state.projects = projects;
      updateWorkspaceUi();
      populateProjectPicker();
      log('info', 'workspace.opened', { name: handle.name, projects: real.map((item) => item.name), template: projects.some((item)=>item.name==='TEMPLATE') });
      if (real.length === 1) await loadProject(real[0]);
      else showProjectModal(real);
    } catch (error) {
      if (error && error.name === 'AbortError') {
        log('info', 'workspace.picker_cancelled', { error_type: error.name });
        toast('Folder selection was cancelled or not completed. You can also use “Open folder copy”.', 'warning');
        return;
      }
      log('error', 'workspace.open_failed', { message: error.message });
      toast('Could not open conference: ' + error.message, 'error');
    }
  }

  async function discoverFsProjects(rootHandle) {
    const result = [];
    if (await hasFile(rootHandle, 'conference.yaml')) {
      result.push({ name: rootHandle.name, handle: rootHandle, direct: true, prefix: '' });
      return result;
    }
    for await (const [name, handle] of rootHandle.entries()) {
      if (handle.kind !== 'directory') continue;
      if (await hasFile(handle, 'conference.yaml')) result.push({ name, handle, direct: false, prefix: name + '/' });
    }
    return result.sort((a, b) => {
      if (a.name === 'TEMPLATE') return 1;
      if (b.name === 'TEMPLATE') return -1;
      return a.name.localeCompare(b.name);
    });
  }

  async function hasFile(dirHandle, name) {
    try { await dirHandle.getFileHandle(name); return true; }
    catch (error) {
      if (error.name === 'NotFoundError' || error.name === 'TypeMismatchError') return false;
      throw error;
    }
  }

  function discoverMemoryProjects(directName, files) {
    const candidates = [];
    const sourceFiles = files || state.memoryFiles;
    sourceFiles.forEach((_file, path) => {
      const normalized = String(path || '').replace(/\\/g, '/');
      if (normalized === 'conference.yaml') {
        candidates.push({ name: directName || 'Imported conference', prefix: '', direct: true });
        return;
      }
      if (!normalized.endsWith('/conference.yaml')) return;
      const prefix = normalized.slice(0, -'conference.yaml'.length);
      const directory = prefix.replace(/\/$/, '');
      const name = directory.split('/').filter(Boolean).pop() || directName || 'Imported conference';
      candidates.push({ name, prefix, direct: false });
    });
    return uniqueProjects(candidates);
  }

  async function importFolderFallback(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    state.mode = 'memory';
    state.rootHandle = null;
    state.projectHandle = null;
    state.memoryFiles.clear();
    state.memoryOverrides.clear();
    state.memoryDeleted.clear();

    const topName = files[0].webkitRelativePath ? files[0].webkitRelativePath.split('/')[0] : 'Imported folder';
    state.workspaceName = topName;
    files.forEach((file) => {
      const raw = file.webkitRelativePath || file.name;
      const parts = raw.split('/');
      const relative = parts.length > 1 ? parts.slice(1).join('/') : raw;
      state.memoryFiles.set(relative, file);
    });

    state.projects = discoverMemoryProjects(topName);
    const real = realConferenceProjects(state.projects);
    updateWorkspaceUi();
    populateProjectPicker();
    log('info', 'workspace.opened_fallback', { name: topName, files: files.length, projects: real.map((item) => item.name) });

    if (!real.length) {
      toast('No conference.yaml found in the selected folder.', 'error');
      return;
    }
    if (real.length === 1) await loadProject(real[0]);
    else showProjectModal(real);
  }

  function zipBaseName(filename) {
    const base = String(filename || 'Imported conference').replace(/\.zip$/i, '');
    return base.replace(/-v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/i, '') || 'Imported conference';
  }

  function commonZipRoot(entries) {
    const names = Array.from(entries.keys()).filter(Boolean);
    if (!names.length) return '';
    const roots = names.map((name) => String(name).split('/').filter(Boolean)[0] || '');
    return roots[0] && roots.every((root) => root === roots[0]) ? roots[0] : '';
  }

  async function importConferenceZip(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;

    try {
      if (isDirty()) {
        const proceed = await totemConfirm('Unsaved changes','There are unsaved changes in the current conference. Import another ZIP anyway?','Import anyway',{danger:true});
        if (!proceed) return;
      }

      const rawEntries = await readZipEntries(file);
      if (!rawEntries.size) throw new Error('The ZIP is empty.');
      const archiveRoot = commonZipRoot(rawEntries);
      const entries = stripBundleRoot(rawEntries);
      const directName = archiveRoot || zipBaseName(file.name);
      const importedFiles = new Map();
      entries.forEach((blob, path) => { if (path) importedFiles.set(path, blob); });
      const projects = discoverMemoryProjects(directName, importedFiles);
      const real = realConferenceProjects(projects);
      if (!real.length) throw new Error('No conference.yaml found in the ZIP.');

      state.mode = 'memory';
      state.rootHandle = null;
      state.projectHandle = null;
      state.memoryFiles = importedFiles;
      state.memoryOverrides.clear();
      state.memoryDeleted.clear();
      state.workspaceName = file.name;
      state.projects = projects;
      state.yamlDirty = false;
      state.regformDirty = false;
      state.people.dirty = false;
      state.program.dirty = false;
      state.versionDirty = false;
      state.sectionYamlDirty = false;
      state.sectionYamlDrafts.clear();

      updateWorkspaceUi();
      populateProjectPicker();
      log('info', 'workspace.opened_zip', { archive: file.name, files: state.memoryFiles.size, projects: real.map((item) => item.name) });
      toast('ZIP imported. Changes will be kept in this session until you export a new ZIP.', 'success');
      if (real.length === 1) await loadProject(real[0]);
      else showProjectModal(real);
    } catch (error) {
      log('error', 'workspace.zip_import_failed', { archive: file.name, message: error.message });
      toast('Could not import ZIP: ' + error.message, 'error');
    }
  }

  function uniqueProjects(items) {
    const map = new Map();
    items.forEach((item) => map.set(item.prefix, item));
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }

  function populateProjectPicker() {
    const real = realConferenceProjects(state.projects);
    els.projectPicker.replaceChildren();
    real.forEach((project) => {
      const option = document.createElement('option');
      option.value = project.name;
      option.textContent = project.name;
      els.projectPicker.append(option);
    });
    els.projectPickerWrap.classList.toggle('hidden', real.length < 2);
    // New conference is a first-class action and is always available.
    els.newConferenceBtn.classList.remove('hidden');
  }

  function showProjectModal(projects) {
    els.modalTitle.textContent = 'Select conference';
    els.modalBody.replaceChildren();
    realConferenceProjects(projects).forEach((project) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'project-choice';
      const text = document.createElement('span');
      const strong = document.createElement('strong');
      strong.textContent = project.name;
      const small = document.createElement('small');
      small.textContent = project.name === 'TEMPLATE' ? 'Placeholder base for future conferences' : 'Conference project';
      text.append(strong, small);
      const arrow = document.createElement('span');
      arrow.textContent = 'Open';
      button.append(text, arrow);
      button.addEventListener('click', async () => { closeModal(); await loadProject(project); });
      els.modalBody.append(button);
    });
    els.modalBackdrop.classList.remove('hidden');
  }

  function closeModal() { els.modalBackdrop.classList.add('hidden'); els.modalBackdrop.classList.remove('preview-modal','people-import-modal','image-picker-modal'); }

  function showCanvasPreview(canvas, title) {
    if (!canvas) return;
    els.modalTitle.textContent = title || 'Document preview';
    els.modalBody.replaceChildren();
    const image = document.createElement('img');
    image.alt = title || 'Document preview';
    image.src = canvas.toDataURL('image/png');
    els.modalBody.append(image);
    els.modalBackdrop.classList.add('preview-modal');
    els.modalBackdrop.classList.remove('hidden');
  }

  function documentPreviewStage(canvas, title, note) {
    const stage = div('doc-preview-stage');
    const head = div('doc-preview-head');
    const label = document.createElement('div');
    const strong = document.createElement('b'); strong.textContent = title;
    const meta = document.createElement('span'); meta.textContent = note ? ' · ' + note : '';
    label.append(strong, meta);
    const zoom = button('Open preview','button ghost');
    zoom.addEventListener('click', () => showCanvasPreview(canvas, title));
    head.append(label, zoom);
    const holder = div('doc-preview-canvas'); holder.append(canvas);
    // Keep the rendered document's intrinsic aspect ratio in every preview size.
    canvas.classList.add('doc-preview-page');
    canvas.style.removeProperty('width');
    canvas.style.removeProperty('height');
    const pageWidth = Math.max(1, Number(canvas.width) || 1), pageHeight = Math.max(1, Number(canvas.height) || 1);
    const pageRatio = pageWidth / pageHeight;
    canvas.style.aspectRatio = pageWidth + ' / ' + pageHeight;
    holder.style.setProperty('--doc-page-aspect', pageWidth + ' / ' + pageHeight);
    holder.style.setProperty('--doc-preview-width', pageRatio >= 1 ? '820px' : '560px');
    canvas.tabIndex = 0; canvas.title = 'Click to enlarge';
    canvas.addEventListener('click', () => showCanvasPreview(canvas, title));
    canvas.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showCanvasPreview(canvas, title); } });
    stage.append(head, holder);
    return stage;
  }

  async function selectProjectByName(name) {
    const project = state.projects.find((item) => item.name === name);
    if (project) await loadProject(project);
  }

  async function loadProject(project) {
    if (isDirty()) {
      const proceed = await totemConfirm('Unsaved changes','There are unsaved changes in the current conference. Open another project anyway?','Open anyway',{danger:true});
      if (!proceed) {
        if (state.projectName) els.projectPicker.value = state.projectName;
        return;
      }
    }
    clearObjectUrls();
    state.projectName = project.name;
    state.projectPrefix = project.prefix || '';
    state.projectHandle = project.handle || null;
    state.yamlDirty = false;
    state.regformDirty = false;
    state.people.dirty = false;
    state.peopleDemo.active = false;
    state.peopleDemo.snapshot = null;
    state.program.dirty = false;
    state.sectionYamlDirty = false;

    try {
      const yamlText = await readProjectText('conference.yaml');
      const config = window.YamlLite.parse(yamlText);
      const legacyForm = config && config.registration && typeof config.registration === 'object' ? config.registration.form : null;
      if (config && config.registration && typeof config.registration === 'object' && Object.prototype.hasOwnProperty.call(config.registration, 'form')) delete config.registration.form;

      let regformText = await readProjectText('regform/settings.yaml').catch(() => '');
      let regformConfig = null;
      if (regformText.trim()) {
        const regformDocument = window.YamlLite.parse(regformText);
        regformConfig = regformDocument && regformDocument.regform;
        if (!regformConfig || typeof regformConfig !== 'object' || Array.isArray(regformConfig)) throw new Error('regform/settings.yaml must contain a top-level regform mapping.');
      } else if (legacyForm && typeof legacyForm === 'object' && !Array.isArray(legacyForm)) {
        regformConfig = legacyForm;
        regformText = stringifyRegformSettings(regformConfig);
        state.regformDirty = true;
        state.yamlDirty = true;
      }
      state.regformConfig = regformConfig || Object.create(null);
      state.regformYamlText = regformText.trim() ? regformText : stringifyRegformSettings(state.regformConfig);
      state.yamlText = legacyForm ? window.YamlLite.stringify(config) : yamlText;
      state.config = attachRegformConfig(config, state.regformConfig);
      state.people.path = getConfig('runtime.people_csv', 'data/people.csv');
      state.program.path = getConfig('runtime.program_csv', 'data/program.csv');

      const [peopleText, programText, versionText] = await Promise.all([
        readProjectText(state.people.path).catch(() => ''),
        readProjectText(state.program.path).catch(() => ''),
        readProjectText('conference.version.json').catch(() => '')
      ]);
      const peopleParsed = window.CsvUtil.parse(peopleText);
      const programParsed = window.CsvUtil.parse(programText);
      normalizePeopleSheet(peopleParsed);
      normalizeProgramSheet(programParsed);
      state.version = parseVersionMetadata(versionText);
      state.versionDirty = false;

      els.yamlEditor.value = state.yamlText;
      setYamlStatus('valid', 'YAML loaded and valid');
      if (!Object.prototype.hasOwnProperty.call(state.config, state.activeSettingsSection)) state.activeSettingsSection = Object.keys(state.config)[0] || '';
      await loadAssets();
      renderSettings();
      renderSpreadsheet('people');
      renderSpreadsheet('program');
      resetDocumentSelections();
      renderImportantDates();
      renderContentChecks();
      renderDocuments();
      renderOverview();
      updateProjectUi();
      updateSaveState();
      log('info', 'project.loaded', {
        project: state.projectName,
        people: state.people.rows.length,
        programRows: state.program.rows.length,
        assets: state.assets.length,
        mode: state.mode,
        version: state.version.version,
        regformSettings: 'regform/settings.yaml'
      });
      if (legacyForm) toast('Opened ' + state.projectName + ' · legacy registration.form migrated to regform/settings.yaml (save required).', 'success');
      else toast('Opened ' + state.projectName, 'success');
    } catch (error) {
      state.config = null;
      log('error', 'project.load_failed', { project: project.name, message: error.message });
      toast('Could not load project: ' + error.message, 'error');
      updateProjectUi();
    }
  }


  function normalizedHeaderKey(value) {
    return String(value || '').trim().toLowerCase().normalize('NFKD')
      .replace(/[àáâãäå]/g, 'a').replace(/[èéêë]/g, 'e').replace(/[ìíîï]/g, 'i')
      .replace(/[òóôõö]/g, 'o').replace(/[ùúûü]/g, 'u')
      .replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function peopleHeaderTarget(header) {
    const key = normalizedHeaderKey(header);
    const aliases = {
      'first name':'First Name','firstname':'First Name','first':'First Name','given name':'First Name','given':'First Name','nome':'First Name',
      'last name':'Last Name','lastname':'Last Name','last':'Last Name','surname':'Last Name','family name':'Last Name','cognome':'Last Name',
      'category':'Category','categoria':'Category','group':'Category','gruppo':'Category',
      'role':'Role','roles':'Role','ruolo':'Role','ruoli':'Role','position':'Role','positions':'Role',
      'presentation title':'Presentation Title','talk title':'Presentation Title','abstract title':'Presentation Title','contribution title':'Presentation Title','paper title':'Presentation Title','lecture title':'Presentation Title','titolo presentazione':'Presentation Title','titolo talk':'Presentation Title','titolo abstract':'Presentation Title','titolo contributo':'Presentation Title',
      'presentation type':'Presentation Type','talk type':'Presentation Type','contribution type':'Presentation Type','presentation format':'Presentation Type','format':'Presentation Type','tipo presentazione':'Presentation Type','tipo contributo':'Presentation Type',
      'affiliation':'Affiliation','affiliazione':'Affiliation','institution':'Affiliation','institute':'Affiliation','organization':'Affiliation','organisation':'Affiliation','university':'Affiliation','ente':'Affiliation',
      'country':'Country','nazione':'Country','nation':'Country',
      'image':'Image','immagine':'Image','photo':'Image','foto':'Image','picture':'Image','avatar':'Image','face':'Image',
      'visible':'Visible','visibile':'Visible','enabled':'Visible','active':'Visible','show':'Visible','shown':'Visible'
    };
    return aliases[key] || '';
  }

  function splitFullName(value) {
    const parts = String(value || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length <= 1) return { first: parts[0] || '', last: '' };
    return { first: parts.slice(0, -1).join(' '), last: parts[parts.length - 1] };
  }

  function canonicalPeopleRoleToken(value) {
    const raw = String(value || '').trim().replace(/\s+/g, ' ');
    if (!raw) return '';
    const key = normalizedHeaderKey(raw);
    if (!key || key === '???' || /^talk\b/.test(key) || /\bnon viene\b/.test(key)) return '';
    if (['speaker','spaeker','spekaer'].includes(key)) return 'Speaker';
    if (['online speaker','online spaeker','online spekaer'].includes(key)) return 'Online Speaker';
    if (['invited','invited speaker'].includes(key)) return 'Invited Speaker';
    if (['oral','oral speaker'].includes(key)) return 'Oral Speaker';
    if (['poster','poster speaker','poster presenter'].includes(key)) return 'Poster Presenter';
    if (['organizing committee','organising committee','organizing committee member','organizing committee members','organising committee member','organising committee members'].includes(key)) return 'Organizing Committee';
    if (['program committee chairman','program committee chair','program committee chairperson','scientific chairman','scientific chair'].includes(key)) return 'Program Committee Chairman';
    if (['program committee co chairman','program committee co chair','program committee cochairman','program committee cochair'].includes(key)) return 'Program Committee Co-chairman';
    if (['committee','committe','commitee','committee member','committee members','program committee','program committee member','program committee members'].includes(key)) return 'Program Committee';
    // Historical MIFP sheets use plain "Organizer" for local/on-site organizers.
    if (['organizer','organiser','local organizer','local organiser'].includes(key)) return 'Local Organizer';
    if (['co chairman','co chair','cochairman','cochair','vice chairman','vice chair'].includes(key)) return 'Co-chairman';
    if (['chair','chairman','chairperson','conference chairman','conference chair'].includes(key)) return 'Chairman';
    if (key === 'staff') return 'Staff';
    return raw;
  }

  function normalizeRoleList(value) {
    let text = String(value || '')
      .replace(/\bspaeker\b/gi, 'Speaker')
      .replace(/\bspekaer\b/gi, 'Speaker')
      .replace(/\bcommitee\b/gi, 'Committee')
      .replace(/\bcommitte\b/gi, 'Committee')
      .replace(/\bnon\s+viene\b/gi, '')
      .replace(/\bcommittee\s+invited\b/gi, 'Committee, Invited');
    const tokens = text.split(/[;,|]/).map((item) => String(item || '').trim()).filter(Boolean);
    const keys = tokens.map(normalizedHeaderKey);
    const genericCommitteeKeys = new Set(['committee','committee member','committee members']);
    const explicitProgram = keys.some((key) => ['program committee','program committee member','program committee members','program committee chairman','program committee chair','program committee chairperson','program committee co chairman','program committee co chair','program committee cochairman','program committee cochair'].includes(key));
    const explicitOrganizing = keys.some((key) => ['organizing committee','organising committee','organizing committee member','organizing committee members','organising committee member','organising committee members'].includes(key));
    const localOrganizer = keys.some((key) => ['organizer','organiser','local organizer','local organiser'].includes(key));
    const coChair = keys.some((key) => ['co chairman','co chair','cochairman','cochair','vice chairman','vice chair'].includes(key));
    const chair = keys.some((key) => ['chair','chairman','chairperson','conference chairman','conference chair'].includes(key));
    const specialCommitteeRole = explicitOrganizing || localOrganizer || coChair || chair;
    const roles = [];
    const add = (role) => { if (role && !roles.includes(role)) roles.push(role); };

    tokens.forEach((token, index) => {
      const key = keys[index];
      if (genericCommitteeKeys.has(key) && specialCommitteeRole && !explicitProgram) return;
      if (['program committee chairman','program committee chair','program committee chairperson','scientific chairman','scientific chair'].includes(key)) { add('Program Committee'); add('Program Committee Chairman'); return; }
      if (['program committee co chairman','program committee co chair','program committee cochairman','program committee cochair'].includes(key)) { add('Program Committee'); add('Program Committee Co-chairman'); return; }
      if (['chair','chairman','chairperson','conference chairman','conference chair'].includes(key)) {
        if (explicitProgram) { add('Program Committee'); add('Program Committee Chairman'); }
        else { add('Organizing Committee'); add('Chairman'); }
        return;
      }
      if (['co chairman','co chair','cochairman','cochair','vice chairman','vice chair'].includes(key)) {
        if (explicitProgram) { add('Program Committee'); add('Program Committee Co-chairman'); }
        else { add('Organizing Committee'); add('Co-chairman'); }
        return;
      }
      if (['organizing committee','organising committee','organizing committee member','organizing committee members','organising committee member','organising committee members'].includes(key)) { add('Organizing Committee'); return; }
      if (['organizer','organiser','local organizer','local organiser'].includes(key)) { add('Local Organizer'); return; }
      add(canonicalPeopleRoleToken(token));
    });
    return roles.join('; ');
  }

  function peopleResponseState(value) {
    const key = normalizedHeaderKey(value);
    if (!key) return null;
    if (['yes','y','true','1','confirmed','accepted','attending','present','si'].includes(key)) return true;
    if (['no','n','false','0','declined','not attending','cancelled','canceled','absent'].includes(key)) return false;
    return null;
  }

  function isPeopleImportMetadataHeader(header) {
    const key = normalizedHeaderKey(header);
    if (!key || /^(column|colonna) \d+$/.test(key)) return true;
    return [
      'email','e mail','mail','response','reply','confirmed','confirmation','attending','partecipazione','risposta',
      'img','file','invitation','invite','invited by'
    ].includes(key);
  }

  function preparePeopleImport(parsed) {
    if (parsed && parsed.peopleImportMeta) return parsed;
    const sourceHeaders = parsed.headers || [];
    const responseHeader = sourceHeaders.find((header) => ['response','reply','confirmed','confirmation','attending','partecipazione','risposta'].includes(normalizedHeaderKey(header))) || '';
    const rows = (parsed.rows || []).slice();
    return Object.assign({}, parsed, {
      rows,
      peopleImportMeta: { sourceRows: rows.length, responseHeader, skippedByResponse: 0 }
    });
  }

  function normalizePeopleData(parsed) {
    const prepared = preparePeopleImport(parsed);
    const baseHeaders = ['First Name','Last Name','Category','Role','Affiliation','Country','Presentation Title','Presentation Type','Image','Visible'];
    const sourceHeaders = prepared.headers || [];
    const targetBySource = new Map();
    let fullNameHeader = '';
    const keys = sourceHeaders.map(normalizedHeaderKey);
    const hasExplicitLastName = keys.some((key) => ['last name','lastname','surname','family name','cognome'].includes(key));
    const hasExplicitFirstName = keys.some((key) => ['first name','firstname','first','given name','given','nome'].includes(key));
    sourceHeaders.forEach((header) => {
      const key = normalizedHeaderKey(header);
      if (key === 'name' && hasExplicitLastName && !hasExplicitFirstName) {
        targetBySource.set(header, 'First Name');
        return;
      }
      if (['name','full name','fullname','nominativo','person'].includes(key)) fullNameHeader = header;
      const target = peopleHeaderTarget(header);
      if (target) targetBySource.set(header, target);
    });
    const ignored = sourceHeaders.filter((header) => isPeopleImportMetadataHeader(header));
    const extra = sourceHeaders.filter((header) => !targetBySource.has(header) && header !== fullNameHeader && !isPeopleImportMetadataHeader(header) && normalizedHeaderKey(header) !== 'visibile');
    const headers = baseHeaders.concat(extra.filter((header, i, arr) => arr.indexOf(header) === i));
    const rows = (prepared.rows || []).map((source) => {
      const row = Object.create(null);
      headers.forEach((header) => { row[header] = ''; });
      sourceHeaders.forEach((sourceHeader) => {
        const target = targetBySource.get(sourceHeader);
        if (target) row[target] = source[sourceHeader] == null ? '' : String(source[sourceHeader]).trim();
        else if (headers.includes(sourceHeader)) row[sourceHeader] = source[sourceHeader] == null ? '' : String(source[sourceHeader]).trim();
      });
      if (fullNameHeader && !row['First Name'] && !row['Last Name']) {
        const name = splitFullName(source[fullNameHeader]); row['First Name'] = name.first; row['Last Name'] = name.last;
      }
      row.Role = normalizeRoleList(row.Role);
      row.Visible = row.Visible ? normalizeBooleanString(row.Visible) : 'true';
      if (!row.Category) row.Category = inferPeopleCategory(row.Role);
      return row;
    }).filter((row) => row['First Name'] || row['Last Name']);
    return { headers, rows, ignored, sourceHeaders, meta: prepared.peopleImportMeta || null };
  }

  function normalizePeopleSheet(parsed) {
    const normalized = normalizePeopleData(parsed);
    state.people.headers = normalized.headers;
    state.people.rows = normalized.rows;
    log('debug', 'people.columns_normalized', {
      source: normalized.sourceHeaders,
      target: normalized.headers,
      ignored: normalized.ignored,
      sourceRows: normalized.meta ? normalized.meta.sourceRows : normalized.rows.length,
      importedRows: normalized.rows.length,
      skippedByResponse: 0
    });
  }

  function programHeaderTarget(header) {
    const key = normalizedHeaderKey(header);
    const aliases = {
      'day':'Day','giorno':'Day','date':'Date','data':'Date',
      'start':'Start Time','start time':'Start Time','from':'Start Time','inizio':'Start Time','ora inizio':'Start Time',
      'end':'End Time','end time':'End Time','to':'End Time','fine':'End Time','ora fine':'End Time',
      'type':'Type','kind':'Type','item type':'Type','tipo':'Type','categoria':'Type',
      'title':'Title','titolo':'Title','event':'Title','session':'Title','item':'Title','cosa':'Title',
      'speaker':'Speaker','speakers':'Speaker','relatore':'Speaker','relatori':'Speaker','presenter':'Speaker',
      'affiliation':'Affiliation','institution':'Affiliation','affiliazione':'Affiliation',
      'chair':'Chair','chairperson':'Chair','moderator':'Chair','moderatore':'Chair',
      'location':'Location','room':'Location','venue':'Location','luogo':'Location','sala':'Location',
      'notes':'Notes','note':'Notes','description':'Notes','descrizione':'Notes',
      'visible':'Visible','visibile':'Visible','enabled':'Visible','active':'Visible'
    };
    return aliases[key] || '';
  }

  function isTechnicalProgramHeader(header) {
    const key = normalizedHeaderKey(header);
    return ['id','parent id','parentid','child id','childid','children','node id','nodeid','edge id','edgeid'].includes(key);
  }

  function normalizeProgramSheet(parsed) {
    const preferred = ['Day','Date','Start Time','End Time','Type','Title','Speaker','Affiliation','Chair','Location','Notes','Visible'];
    const sourceHeaders = parsed.headers || [];
    const mapped = new Map();
    sourceHeaders.forEach((header) => { const target = programHeaderTarget(header); if (target) mapped.set(header, target); });
    const hiddenTech = sourceHeaders.filter(isTechnicalProgramHeader);
    const extras = sourceHeaders.filter((header) => !mapped.has(header) && !isTechnicalProgramHeader(header));
    state.program.headers = preferred.concat(extras.filter((header) => !preferred.includes(header)));
    state.program.rows = (parsed.rows || []).map((source) => {
      const row = Object.create(null); state.program.headers.forEach((header) => { row[header] = ''; });
      sourceHeaders.forEach((sourceHeader) => {
        const target = mapped.get(sourceHeader);
        if (target) row[target] = source[sourceHeader] == null ? '' : String(source[sourceHeader]);
        else if (state.program.headers.includes(sourceHeader)) row[sourceHeader] = source[sourceHeader] == null ? '' : String(source[sourceHeader]);
      });
      row.Visible = row.Visible ? normalizeBooleanString(row.Visible) : 'true';
      return row;
    });
    if (hiddenTech.length) log('info', 'program.graph_columns_ignored', { columns: hiddenTech });
  }

  function inferPeopleCategory(role) {
    const value = String(role || '').toLowerCase();
    if (value.includes('school') || value.includes('lecturer')) return 'Lecturer';
    if (value.includes('committee')) return 'Committee';
    if (value.includes('organizer')) return 'Organizer';
    if (value.includes('poster') || value.includes('oral')) return 'Presenter';
    if (value.includes('speaker')) return 'Speaker';
    return 'Other';
  }

  function parseVersionMetadata(text) {
    const fallbackVersion = String(getConfig('site.version', getConfig('template.version', '1.0')) || '1.0');
    const normalized = /^\d+\.\d+\.\d+$/.test(fallbackVersion) ? fallbackVersion : (/^\d+\.\d+$/.test(fallbackVersion) ? fallbackVersion + '.0' : '1.0.0');
    if (!text) return { schema: 1, version: normalized, status: 'draft', updated_at: '', history: [] };
    try {
      const value = JSON.parse(text);
      return {
        schema: 1,
        version: /^\d+\.\d+\.\d+$/.test(String(value.version || '')) ? String(value.version) : normalized,
        status: ['draft','review','published'].includes(value.status) ? value.status : 'draft',
        updated_at: String(value.updated_at || ''),
        history: Array.isArray(value.history) ? value.history.slice(0, 40) : []
      };
    } catch (_) {
      return { schema: 1, version: normalized, status: 'draft', updated_at: '', history: [] };
    }
  }

  function updateWorkspaceUi() {
    els.workspaceName.textContent = state.workspaceName || 'No folder selected';
  }

  function updateProjectUi() {
    const loaded = Boolean(state.config);
    els.projectName.textContent = loaded ? state.projectName : 'No conference open';
    if (state.projectName) els.projectPicker.value = state.projectName;
    els.welcomePanel.classList.toggle('hidden', loaded);
    els.overviewContent.classList.toggle('hidden', !loaded);
    els.settingsEmpty.classList.toggle('hidden', loaded);
    els.settingsWorkbench.classList.toggle('hidden', !loaded);
    if (els.regformEmpty) els.regformEmpty.classList.toggle('hidden', loaded);
    if (els.regformContent) els.regformContent.classList.toggle('hidden', !loaded);
    els.assetsEmpty.classList.toggle('hidden', loaded);
    els.assetsContent.classList.toggle('hidden', !loaded);
    els.yamlEmpty.classList.toggle('hidden', loaded);
    els.yamlEditorWrap.classList.toggle('hidden', !loaded);
    [els.saveAllBtn, els.exportZipBtn, els.exportMifpZipTopBtn, els.previewSiteBtn, els.refreshMifpExportBtn, els.exportMifpJsonlBtn, els.exportMifpZipBtn, els.saveYamlFromSettingsBtn, els.saveRegformBtn, els.refreshAssetsBtn, els.cleanUnusedAssetsBtn, els.refreshChecksBtn, els.savePeopleBtn, els.saveProgramBtn, els.saveDatesBtn, els.validateYamlBtn, els.downloadYamlBtn, els.saveYamlBtn, els.bumpPatchBtn, els.bumpMinorBtn, els.bumpMajorBtn, els.saveVersionBtn].forEach((button) => { button.disabled = !loaded; });
    els.openSiteBtn.disabled = !loaded;
    els.previewSiteBtn.disabled = !loaded;
  }

  function getConfig(path, fallback) {
    const parts = String(path || '').split('.');
    let current = state.config;
    for (const part of parts) {
      if (!current || typeof current !== 'object' || !(part in current)) return fallback;
      current = current[part];
    }
    return current == null ? fallback : current;
  }


  function isRegformPath(path) {
    return Array.isArray(path) && path[0] === 'registration' && path[1] === 'form';
  }


  function displayConfigPath(path) {
    if (isRegformPath(path)) return ['regform'].concat(path.slice(2)).join('.');
    return (path || []).join('.');
  }

  function attachRegformConfig(config, regformConfig) {
    const target = config && typeof config === 'object' ? config : Object.create(null);
    if (!target.registration || typeof target.registration !== 'object' || Array.isArray(target.registration)) target.registration = Object.create(null);
    target.registration.form = regformConfig && typeof regformConfig === 'object' && !Array.isArray(regformConfig) ? regformConfig : Object.create(null);
    return target;
  }

  function conferenceConfigForStorage(config) {
    const copy = deepClone(config || state.config || Object.create(null));
    if (copy.registration && typeof copy.registration === 'object' && !Array.isArray(copy.registration)) delete copy.registration.form;
    return copy;
  }

  function conferenceSectionForStorage(section) {
    const copy = deepClone(state.config && state.config[section]);
    if (section === 'registration' && copy && typeof copy === 'object' && !Array.isArray(copy)) delete copy.form;
    return copy;
  }

  function stringifyRegformSettings(config) {
    const wrapper = Object.create(null);
    wrapper.regform = deepClone(config || Object.create(null));
    return window.YamlLite.stringify(wrapper);
  }

  async function readProjectText(path) {
    const blob = await readProjectBlob(path);
    return blob.text();
  }

  async function readProjectBlob(path) {
    const safePath = normalizeProjectPath(path);
    if (state.mode === 'fs') {
      const handle = await getFileHandleByPath(state.projectHandle, safePath, false);
      return handle.getFile();
    }
    if (state.mode === 'memory') {
      const key = state.projectPrefix + safePath;
      const overrideKey = state.projectPrefix + safePath;
      if (state.memoryDeleted.has(key)) throw new Error('File not found: ' + safePath);
      if (state.memoryOverrides.has(overrideKey)) return state.memoryOverrides.get(overrideKey);
      const file = state.memoryFiles.get(key);
      if (!file) throw new Error('File not found: ' + safePath);
      return file;
    }
    throw new Error('No project open');
  }

  async function writeProjectText(path, text) {
    const safePath = normalizeProjectPath(path);
    if (state.mode === 'fs') {
      const handle = await getFileHandleByPath(state.projectHandle, safePath, true);
      const writable = await handle.createWritable();
      await writable.write(String(text));
      await writable.close();
      return 'saved';
    }
    state.memoryDeleted.delete(state.projectPrefix + safePath);
    state.memoryOverrides.set(state.projectPrefix + safePath, new Blob([String(text)], { type: safePath.endsWith('.csv') ? 'text/csv;charset=utf-8' : 'text/plain;charset=utf-8' }));
    return 'staged';
  }

  async function writeProjectBlob(path, blob) {
    const safePath = normalizeProjectPath(path);
    if (state.mode === 'fs') {
      const handle = await getFileHandleByPath(state.projectHandle, safePath, true);
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return 'saved';
    }
    state.memoryDeleted.delete(state.projectPrefix + safePath);
    state.memoryOverrides.set(state.projectPrefix + safePath, blob);
    return 'staged';
  }

  async function deleteProjectPath(path) {
    const safePath = normalizeProjectPath(path);
    if (state.mode === 'fs') {
      const parts = safePath.split('/').filter(Boolean);
      const name = parts.pop();
      let dir = state.projectHandle;
      for (const part of parts) dir = await dir.getDirectoryHandle(part);
      await dir.removeEntry(name);
      return 'deleted';
    }
    if (state.mode === 'memory') {
      const key = state.projectPrefix + safePath;
      state.memoryOverrides.delete(key);
      state.memoryDeleted.add(key);
      return 'staged-delete';
    }
    throw new Error('No project open');
  }

  async function getFileHandleByPath(root, path, create) {
    if (!root) throw new Error('No project directory handle');
    const parts = normalizeProjectPath(path).split('/').filter(Boolean);
    if (!parts.length) throw new Error('Invalid file path');
    let dir = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      dir = await dir.getDirectoryHandle(parts[i], { create: Boolean(create) });
    }
    return dir.getFileHandle(parts[parts.length - 1], { create: Boolean(create) });
  }

  function normalizeProjectPath(path) {
    const value = String(path || '').replace(/\\/g, '/').replace(/^\.\//, '').replace(/^\/+/, '');
    const parts = value.split('/').filter(Boolean);
    if (!parts.length || parts.some((part) => part === '..' || part === '.')) throw new Error('Unsafe project path: ' + path);
    return parts.join('/');
  }

  async function saveAll() {
    if (!state.config) return;
    try {
      if (state.sectionYamlDrafts && state.sectionYamlDrafts.size) { toast('Apply or reset the raw YAML drafts before saving.', 'error'); switchView('settings'); return; }
      if (state.sectionYamlDirty && !applySectionYamlFromEditor()) return;
      if (state.yamlDirty) await saveYaml();
      if (state.regformDirty) { const ok = await saveRegformSettings(); if (!ok) return; }
      if (state.people.dirty) await saveSheet('people');
      if (state.program.dirty) await saveSheet('program');
      if (state.versionDirty) await saveVersionMetadata();
      if (!isDirty()) toast(state.mode === 'fs' ? 'All changes saved.' : 'Changes staged for ZIP export.', 'success');
    } catch (error) {
      log('error', 'save_all.failed', { message: error.message });
      toast('Save failed: ' + error.message, 'error');
    }
  }

  function isDirty() { return Boolean(state.yamlDirty || state.regformDirty || state.people.dirty || state.program.dirty || state.versionDirty || state.sectionYamlDirty); }

  function updateSaveState() {
    const loaded = Boolean(state.config);
    els.saveAllBtn.disabled = !loaded;
    if (!loaded) {
      els.saveState.className = 'save-state neutral';
      els.saveState.textContent = 'No project open';
      return;
    }
    if (isDirty()) {
      els.saveState.className = 'save-state dirty';
      els.saveState.textContent = state.mode === 'fs' ? 'Unsaved changes' : 'Changes ready to export';
    } else {
      els.saveState.className = 'save-state saved';
      els.saveState.textContent = state.mode === 'fs' ? 'Saved' : 'Loaded in fallback mode';
    }
  }

  function renderOverview() {
    if (!state.config) return;
    const registrationEnabled = Boolean(getConfig('registration.enabled', false));
    const cards = [
      ['Conference', getConfig('site.title', state.projectName), getConfig('conference.full_name', '')],
      ['Dates', getConfig('conference.date_label', 'TBC'), [getConfig('conference.start_date', ''), getConfig('conference.end_date', '')].filter(Boolean).join(' → ')],
      ['Location', [getConfig('conference.city', ''), getConfig('conference.country', '')].filter(Boolean).join(', ') || 'TBC', getConfig('conference.venue', 'TBC')],
      ['Registration', registrationEnabled ? 'Enabled' : 'To be defined', registrationEnabled ? getConfig('conference.registration_url', 'regform/') : getConfig('registration.unavailable_url', 'registration-tbd.html')],
      ['People rows', String(state.people.rows.length), state.people.path],
      ['Program rows', String(state.program.rows.length), state.program.path],
      ['Theme', String(getConfig('appearance.default_theme', '')), String(getConfig('appearance.default_palette', '')) + ' palette'],
      ['Assets', String(state.assets.length), 'Local project files'],
      ['Version', 'v' + (state.version.version || '1.0.0'), state.version.status || 'draft']
    ];
    els.overviewCards.replaceChildren();
    cards.forEach(([label, value, note]) => {
      const card = div('stat-card');
      card.append(div('stat-label', label), div('stat-value', value || 'TBC'), div('stat-note', note || ''));
      els.overviewCards.append(card);
    });

    const files = [
      ['Project', state.projectName],
      ['Mode', state.mode === 'fs' ? 'Direct read/write' : 'Fallback import/export'],
      ['Public config', 'conference.yaml'],
      ['Regform settings', 'regform/settings.yaml'],
      ['People', state.people.path],
      ['Program', state.program.path],
      ['Registration data', 'regform/registrations/'],
      ['Network dependencies', 'None']
    ];
    renderVersionPanel();
        els.overviewFiles.replaceChildren();
    files.forEach(([term, value]) => {
      const dt = document.createElement('dt'); dt.textContent = term;
      const dd = document.createElement('dd'); dd.textContent = value;
      els.overviewFiles.append(dt, dd);
    });
  }

  const SETTINGS_PAGES = [
    { id:'home', label:'Home', file:'index.html', sections:['hero','home_intro','quantum_school','at_glance','statistics','countdown','important_dates','registration','home_venue','home_program','abstract_submission','sponsor_packages','organizers','committee','home_speakers','home_venue_accommodation','home_visa','home_social'] },
    { id:'program', label:'Program', file:'program.html', sections:['program'] },
    { id:'speakers', label:'Speakers & People', file:'speakers.html', sections:['people','committee'] },
    { id:'venue', label:'Venue', file:'venue.html', sections:['venue'] },
    { id:'accommodation', label:'Accommodation', file:'accommodation.html', sections:['accommodation'] },
    { id:'social', label:'Social Program', file:'social_program.html', sections:['social_program'] },
    { id:'travel', label:'Travel', file:'travel.html', sections:['travel'] },
    { id:'registration', label:'Registration', file:'registration.html', sections:['registration'] },
    { id:'privacy', label:'Privacy', file:'privacy.html', sections:['privacy'] },
    { id:'global', label:'Global site', file:'shared on every page', sections:['site','conference','labels','navigation','assets','appearance','layout','footer','seo','organization'] },
    { id:'technical', label:'Technical', file:'advanced', sections:['template','runtime','security'] }
  ];

  function settingsPageSections(page) {
    if (!state.config) return [];
    const known = new Set(SETTINGS_PAGES.flatMap((item)=>item.sections));
    const base = (page && page.sections || []).filter((key)=>Object.prototype.hasOwnProperty.call(state.config,key));
    if (page && page.id === 'technical') Object.keys(state.config).forEach((key)=>{ if(!known.has(key) && !base.includes(key)) base.push(key); });
    return base;
  }

  function settingsPageAvailable(page) {
    if (!state.config || !page) return false;
    return settingsPageSections(page).length > 0;
  }

  function renderSettings() {
    if (!state.config) return;
    const pages = SETTINGS_PAGES.filter(settingsPageAvailable);
    if (!pages.some((page)=>page.id===state.activeSettingsPage)) state.activeSettingsPage=(pages[0]||{}).id||'';
    renderSettingsSectionNav();
    renderSettingsSection();
  }

  function renderSettingsSectionNav() {
    if (!state.config) return;
    const term = String(els.settingsSectionSearch.value || '').trim().toLowerCase();
    els.settingsSectionNav.replaceChildren();
    SETTINGS_PAGES.filter(settingsPageAvailable).filter((page)=>{
      if (!term) return true;
      return page.label.toLowerCase().includes(term) || page.file.toLowerCase().includes(term) || settingsPageSections(page).some((key)=>humanizeKey(key).toLowerCase().includes(term)||key.toLowerCase().includes(term));
    }).forEach((page) => {
      const btn = button('', 'settings-section-button settings-page-button' + (page.id === state.activeSettingsPage ? ' active' : ''));
      const name=div('settings-page-button-name',page.label), meta=div('settings-page-button-meta',page.file);
      btn.append(name,meta);
      btn.addEventListener('click', async () => {
        if (state.sectionYamlDrafts.size) {
          const discard = await totemConfirm('Discard YAML draft','There are unapplied raw YAML edits on this page. Change page and discard those drafts?','Discard draft',{danger:true});
          if (!discard) return;
          state.sectionYamlDrafts.clear();
        }
        state.activeSettingsPage = page.id;
        renderSettingsSectionNav();
        renderSettingsSection();
      });
      els.settingsSectionNav.append(btn);
    });
  }

  function renderSettingsSection() {
    if (!state.config || !state.activeSettingsPage) return;
    els.settingsForm.replaceChildren();
    els.settingsSectionHead.replaceChildren();
    const page=SETTINGS_PAGES.find((item)=>item.id===state.activeSettingsPage);
    if (!page) return;
    els.saveYamlFromSettingsBtn.textContent = 'Save conference.yaml';
    const titleWrap=div('settings-page-head-copy');
    const title=document.createElement('h2'); title.textContent=page.label;
    const note=document.createElement('p'); note.textContent='Edit the page section by section. Every visual editor is followed immediately by the raw YAML for that same section.';
    titleWrap.append(title,note);
    const code=document.createElement('code'); code.textContent=page.file;
    els.settingsSectionHead.append(titleWrap,code);
    const sections=settingsPageSections(page);
    sections.forEach((section,index)=>els.settingsForm.append(renderSettingsSectionCard(section,index)));
  }

  function renderSettingsSectionCard(section,index) {
    const value=state.config[section];
    const card=div('page-section-card');
    card.id='editor-section-'+section;
    const head=div('page-section-head');
    const copy=div('page-section-head-copy');
    const eyebrow=div('eyebrow','Section '+String(index+1).padStart(2,'0'));
    const title=document.createElement('h3'); title.textContent=humanizeKey(section);
    const key=document.createElement('code'); key.textContent=section;
    copy.append(eyebrow,title,key);
    const enabled=value && typeof value==='object' && !Array.isArray(value) && Object.prototype.hasOwnProperty.call(value,'enabled') ? (value.enabled!==false) : null;
    if(enabled!==null){const badge=div('section-state '+(enabled?'on':'off'),enabled?'Enabled':'Disabled');head.append(copy,badge);} else head.append(copy);
    card.append(head);
    const visual=div('section-visual-editor');
    const experience=renderSettingsExperience(section,value); if(experience) visual.append(experience);
    let visualValue = value;
    if (section === 'program' && value && typeof value === 'object' && !Array.isArray(value) && Object.prototype.hasOwnProperty.call(value,'download')) {
      visualValue = Object.create(null);
      Object.keys(value).forEach((key)=>{ if (key !== 'download') visualValue[key] = value[key]; });
    }
    if (section === 'registration' && value && typeof value === 'object' && !Array.isArray(value) && Object.prototype.hasOwnProperty.call(value,'form')) {
      visualValue = Object.create(null);
      Object.keys(value).forEach((key)=>{ if (key !== 'form') visualValue[key] = value[key]; });
      const regformLink = div('regform-editor-callout');
      const regformCopy = div(''); regformCopy.innerHTML = '<b>Regform settings are separate</b><span>Use the dedicated Regform page in the sidebar to edit PHP form availability, email delivery and organizer recipients.</span>';
      const regformBtn = button('Open Regform','button ghost');
      regformBtn.addEventListener('click',()=>switchView('regform'));
      regformLink.append(regformCopy,regformBtn); visual.append(regformLink);
    }
    visual.append(renderYamlNode(visualValue,[section],section,0));
    card.append(visual);
    card.append(renderInlineSectionYaml(section));
    return card;
  }

  function renderRegformPage() {
    if (!state.config || !els.regformForm || !els.regformSectionHead) return;
    const form = getConfig('registration.form', null);
    els.regformForm.replaceChildren();
    els.regformSectionHead.replaceChildren();
    if (!form || typeof form !== 'object' || Array.isArray(form)) { els.regformForm.append(div('empty-state compact','regform/settings.yaml is missing or invalid')); return; }

    const titleWrap = div('settings-page-head-copy');
    const title = document.createElement('h2'); title.textContent = 'Regform settings';
    const note = document.createElement('p'); note.textContent = 'Private PHP registration settings. This page is independent from the public Registration content in conference.yaml.';
    titleWrap.append(title, note);
    const code = document.createElement('code'); code.textContent = 'regform/settings.yaml';
    els.regformSectionHead.append(titleWrap, code);

    const availabilityCard = div('page-section-card regform-availability-card');
    const availabilityHead = div('page-section-head');
    const availabilityCopy = div('page-section-head-copy');
    availabilityCopy.append(div('eyebrow','Availability'), Object.assign(document.createElement('h3'), { textContent:'Form availability' }), Object.assign(document.createElement('code'), { textContent:'regform.enabled / submit_enabled' }));
    availabilityHead.append(availabilityCopy, div('section-state '+((form.enabled !== false && form.submit_enabled !== false)?'on':'off'),(form.enabled !== false && form.submit_enabled !== false)?'Open':'Closed'));
    const availabilityVisual = div('section-visual-editor');
    const availabilityGrid = div('regform-availability-grid');
    const moduleToggle = document.createElement('label'); moduleToggle.className='regform-confirm-toggle'; moduleToggle.dataset.configPath='registration.form.enabled';
    const moduleCheckbox = document.createElement('input'); moduleCheckbox.type='checkbox'; moduleCheckbox.checked=form.enabled !== false;
    const moduleText = document.createElement('span'); moduleText.innerHTML='<b>Registration form enabled</b><small>Controls whether the separate regform module is available.</small>';
    moduleCheckbox.addEventListener('change',()=>{updateStructuredYaml(['registration','form','enabled'],moduleCheckbox.checked);renderRegformPage();}); moduleToggle.append(moduleCheckbox,moduleText);
    const submitToggle = document.createElement('label'); submitToggle.className='regform-confirm-toggle'; submitToggle.dataset.configPath='registration.form.submit_enabled';
    const submitCheckbox = document.createElement('input'); submitCheckbox.type='checkbox'; submitCheckbox.checked=form.submit_enabled !== false;
    const submitText = document.createElement('span'); submitText.innerHTML='<b>Accept submissions</b><small>Close this while keeping the form/configuration in place.</small>';
    submitCheckbox.addEventListener('change',()=>{updateStructuredYaml(['registration','form','submit_enabled'],submitCheckbox.checked);renderRegformPage();}); submitToggle.append(submitCheckbox,submitText);
    availabilityGrid.append(moduleToggle,submitToggle); availabilityVisual.append(availabilityGrid); availabilityCard.append(availabilityHead,availabilityVisual);

    const mailCard = div('page-section-card regform-mail-card');
    mailCard.id = 'editor-section-regform-mail';
    const mailHead = div('page-section-head');
    const mailCopy = div('page-section-head-copy');
    mailCopy.append(div('eyebrow','Delivery'), Object.assign(document.createElement('h3'), { textContent:'Email delivery' }), Object.assign(document.createElement('code'), { textContent:'regform.mail' }));
    mailHead.append(mailCopy, div('section-state on','Configured'));
    const mailVisual = div('section-visual-editor');
    mailVisual.append(renderRegformMailControls());
    mailCard.append(mailHead, mailVisual);

    const formCard = div('page-section-card');
    formCard.id = 'editor-section-regform';
    const formHead = div('page-section-head');
    const formCopy = div('page-section-head-copy');
    formCopy.append(div('eyebrow','Form'), Object.assign(document.createElement('h3'), { textContent:'Registration form' }), Object.assign(document.createElement('code'), { textContent:'regform/settings.yaml' }));
    const formEnabled = form.enabled !== false && form.submit_enabled !== false;
    formHead.append(formCopy, div('section-state '+(formEnabled?'on':'off'),formEnabled?'Enabled':'Disabled'));
    const formVisual = div('section-visual-editor');
    const formWithoutMail = Object.create(null);
    Object.keys(form).forEach((key)=>{ if (key !== 'mail') formWithoutMail[key] = form[key]; });
    formVisual.append(renderYamlNode(formWithoutMail, ['registration','form'], 'form', 0));
    formCard.append(formHead, formVisual);

    els.regformForm.append(availabilityCard, mailCard, formCard);
  }

  function regformBoundField(labelText, path, helpText, type) {
    const label = document.createElement('label'); label.className = 'regform-mail-field'; label.dataset.configPath = path.map(String).join('.');
    const caption = document.createElement('span'); caption.textContent = labelText;
    const input = document.createElement('input'); input.type = type || 'text'; input.value = String(getAtPath(state.config, path) || '');
    const help = document.createElement('small'); help.textContent = helpText || path.join('.');
    input.addEventListener('input', () => {
      updateStructuredYaml(path, input.value);
      updateEmailInputValidity(input);
    });
    updateEmailInputValidity(input);
    label.append(caption, input, help);
    return label;
  }

  function updateEmailInputValidity(input) {
    if (!input || input.type !== 'email') return;
    const value = String(input.value || '').trim();
    const valid = value === '' || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
    input.classList.toggle('invalid-control', !valid);
    input.setAttribute('aria-invalid', valid ? 'false' : 'true');
  }

  function renderRegformMailControls() {
    const mail = getConfig('registration.form.mail', {});
    const shell = div('regform-mail-settings');
    const intro = div('regform-mail-intro');
    intro.innerHTML = '<p><b>Participant confirmation</b> is sent to the email entered by the requester. <b>Control recipients</b> receive the organizer notification with the submitted details and payment proof. Recipient addresses are not exposed to the participant.</p>';
    shell.append(intro);

    const grid = div('regform-mail-grid');
    grid.append(
      regformBoundField('Sender email', ['registration','form','mail','from_email'], 'Address used in the From header. It must be accepted by the server/PHP mail configuration.', 'email'),
      regformBoundField('Sender name', ['registration','form','mail','from_name'], 'Display name shown to recipients.', 'text'),
      regformBoundField('Reply-to / conference contact', ['registration','form','mail','reply_to_email'], 'Reply-To address used by the registration module. This is separate from conference.email.', 'email'),
      regformBoundField('Subject prefix', ['registration','form','mail','subject_prefix'], 'Prefix used for registration email subjects.', 'text')
    );
    shell.append(grid);

    const toggle = document.createElement('label'); toggle.className = 'regform-confirm-toggle'; toggle.dataset.configPath='registration.form.mail.send_user_confirmation';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = mail.send_user_confirmation !== false;
    const toggleText = document.createElement('span'); toggleText.innerHTML = '<b>Send confirmation to requester</b><small>Uses the email address entered in the registration form.</small>';
    checkbox.addEventListener('change', () => updateStructuredYaml(['registration','form','mail','send_user_confirmation'], checkbox.checked));
    toggle.append(checkbox, toggleText); shell.append(toggle);

    const recipients = div('regform-recipient-panel');
    const recipientsHead = div('regform-recipient-head');
    const recipientsCopy = div(''); recipientsCopy.innerHTML = '<b>Control / organizer recipients</b><span>Each address receives the organizer copy of every valid registration.</span>';
    const add = button('+ Add address', 'button ghost');
    recipientsHead.append(recipientsCopy, add); recipients.append(recipientsHead);
    const list = div('regform-recipient-list'); recipients.append(list);

    function renderRecipients() {
      list.replaceChildren();
      const values = getConfig('registration.form.mail.admin_emails', []);
      const items = Array.isArray(values) ? values : (values ? [String(values)] : []);
      if (!Array.isArray(getConfig('registration.form.mail.admin_emails', []))) {
        setAtPath(state.config, ['registration','form','mail','admin_emails'], items);
        syncRegformSettings('regform.recipients_normalized', { count:items.length });
      }
      if (!items.length) list.append(div('regform-recipient-empty','Add at least one control recipient.'));
      items.forEach((value, index) => {
        const row = div('regform-recipient-row'); row.dataset.configPath='registration.form.mail.admin_emails.'+index;
        const input = document.createElement('input'); input.type='email'; input.value=String(value||''); input.placeholder='name@example.org';
        updateEmailInputValidity(input);
        input.addEventListener('input', () => {
          const current = getAtPath(state.config, ['registration','form','mail','admin_emails']);
          current[index] = input.value;
          updateEmailInputValidity(input);
          syncRegformSettings('regform.recipient_changed', { index });
        });
        const remove = button('Remove','button ghost danger');
        remove.addEventListener('click', () => {
          const current = getAtPath(state.config, ['registration','form','mail','admin_emails']);
          current.splice(index,1);
          syncRegformSettings('regform.recipient_removed',{index});
          renderRecipients();
        });
        row.append(input, remove); list.append(row);
      });
    }

    add.addEventListener('click', () => {
      let current = getConfig('registration.form.mail.admin_emails', []);
      if (!Array.isArray(current)) current = current ? [String(current)] : [];
      current.push('');
      setAtPath(state.config, ['registration','form','mail','admin_emails'], current);
      syncRegformSettings('regform.recipient_added',{count:current.length});
      renderRecipients();
      const inputs = list.querySelectorAll('input[type=email]'); if (inputs.length) inputs[inputs.length-1].focus();
    });
    renderRecipients(); shell.append(recipients);

    const status = div('regform-mail-status');
    shell.append(status);
    const refreshStatus = () => {
      const problem = validateRegformEmailSettings();
      status.className = 'regform-mail-status ' + (problem ? 'invalid' : 'valid');
      status.textContent = problem || 'Email delivery configuration is valid.';
    };
    shell.addEventListener('input', refreshStatus);
    shell.addEventListener('change', refreshStatus);
    shell.addEventListener('click', () => window.setTimeout(refreshStatus, 0));
    refreshStatus();
    return shell;
  }

  function validateRegformEmailSettings(onlyWhenActive) {
    if (!state.config) return '';
    const active = getConfig('registration.enabled', true) !== false && getConfig('registration.form.enabled', true) !== false && getConfig('registration.form.submit_enabled', false) !== false;
    if (onlyWhenActive && !active) return '';
    const emailOk = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || '').trim());
    const sender = getConfig('registration.form.mail.from_email','');
    if (!emailOk(sender)) return 'Set a valid sender email for the registration form.';
    const contact = getConfig('registration.form.mail.reply_to_email','');
    if (!emailOk(contact)) return 'Set a valid regform reply-to email.';
    const rawRecipients = getConfig('registration.form.mail.admin_emails',[]);
    const recipients = Array.isArray(rawRecipients) ? rawRecipients : [rawRecipients];
    const cleaned = recipients.map((item)=>String(item||'').trim()).filter(Boolean);
    if (!cleaned.length) return 'Add at least one control / organizer recipient email.';
    if (cleaned.some((email)=>!emailOk(email))) return 'One or more control / organizer recipient addresses are invalid.';
    return '';
  }

  function renderInlineSectionYaml(section) {
    const panel=div('section-raw-panel inline-section-raw');
    const head=div('section-raw-head');
    const copy=div(''); copy.innerHTML='<div class="eyebrow">Raw YAML · same section</div><h3>'+escapeHtml(humanizeKey(section))+'</h3><p>Advanced escape hatch. Changes here affect only <code>'+escapeHtml(section)+'</code>.</p>';
    const actions=div('inline-actions tight');
    const reset=button('Reset','button ghost');
    const apply=button('Apply YAML','button');
    actions.append(reset,apply); head.append(copy,actions); panel.append(head);
    const status=div('validation-bar neutral','Synchronized with visual fields'); panel.append(status);
    const textarea=document.createElement('textarea'); textarea.className='section-yaml-editor'; textarea.spellcheck=false; textarea.dataset.section=section; textarea.setAttribute('aria-label','Raw YAML for '+section);
    const wrapper=Object.create(null); wrapper[section]=conferenceSectionForStorage(section);
    textarea.value=state.sectionYamlDrafts.get(section)||window.YamlLite.stringify(wrapper);
    textarea.addEventListener('input',()=>{state.sectionYamlDrafts.set(section,textarea.value);status.className='validation-bar neutral';status.textContent='Raw YAML modified · apply to synchronize';updateSaveState();});
    reset.addEventListener('click',()=>{const w=Object.create(null);w[section]=conferenceSectionForStorage(section);textarea.value=window.YamlLite.stringify(w);state.sectionYamlDrafts.delete(section);status.className='validation-bar valid';status.textContent='Draft reset';updateSaveState();});
    apply.addEventListener('click',()=>applyInlineSectionYaml(section,textarea,status));
    panel.append(textarea); return panel;
  }

  function applyInlineSectionYaml(section,textarea,status) {
    try {
      const parsed=window.YamlLite.parse(textarea.value||'');
      if(!parsed||typeof parsed!=='object'||Array.isArray(parsed)) throw new Error('Section YAML must be a mapping');
      if(!Object.prototype.hasOwnProperty.call(parsed,section)) throw new Error('Expected top-level key "'+section+'"');
      state.config[section]=parsed[section]; if(section==='registration') attachRegformConfig(state.config, state.regformConfig); state.sectionYamlDrafts.delete(section); state.sectionYamlDirty=false;
      syncStructuredYaml('setting.section_yaml_applied',{section});
      status.className='validation-bar valid';status.textContent='Applied and synchronized';
      toast('Applied YAML section: '+section,'success');
      renderSettingsSection();
      if(section==='program') renderSpreadsheet('program');
      return true;
    } catch(error) {
      status.className='validation-bar invalid';status.textContent='YAML error · '+error.message;
      log('error','setting.section_yaml_failed',{section,message:error.message});toast('Section YAML error: '+error.message,'error');return false;
    }
  }

  function selectControl(options, current, onChange) {
    const select = document.createElement('select');
    options.forEach((item) => { const o=document.createElement('option'); const value=typeof item==='string'?item:item.value; o.value=value; o.textContent=typeof item==='string'?humanizeKey(item):(item.label||humanizeKey(value)); select.append(o); });
    select.value = String(current == null ? '' : current);
    if (![...select.options].some(o=>o.value===select.value)) { const custom=document.createElement('option'); custom.value=select.value; custom.textContent=select.value+' (custom)'; select.prepend(custom); select.value=custom.value; }
    select.addEventListener('change',()=>onChange(select.value));
    return select;
  }

  function renderProgramDownloadSettings() {
    const cfg = ensureProgramDownloadConfig();
    const shell = div('program-download-editor');
    shell.dataset.configPath = 'program.download';

    const head = div('program-download-editor-head');
    const copy = div('program-download-editor-copy');
    copy.innerHTML = '<div class="eyebrow">Program download</div><h4>Public PDF download</h4><p>Configure the download button shown on the Program page. Enable/disable is independent from the PDF source.</p>';
    const enabledLabel = document.createElement('label');
    enabledLabel.className = 'program-download-toggle';
    enabledLabel.dataset.configPath = 'program.download.enabled';
    const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = cfg.enabled !== false;
    const enabledText = document.createElement('span'); enabledText.innerHTML = '<b>Enabled</b><small>Show the download button</small>';
    enabledLabel.append(enabled, enabledText);
    head.append(copy, enabledLabel); shell.append(head);

    const grid = div('program-download-grid');

    const modeField = document.createElement('label'); modeField.className='program-download-field'; modeField.dataset.configPath='program.download.mode';
    const modeCaption = document.createElement('span'); modeCaption.textContent='Mode';
    const mode = document.createElement('select');
    [
      ['generated','Generate from program.csv'],
      ['local','Use local PDF']
    ].forEach(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;mode.append(option);});
    mode.value = String(cfg.mode || 'generated').toLowerCase() === 'local' ? 'local' : 'generated';
    modeField.append(modeCaption, mode, Object.assign(document.createElement('small'),{textContent:'Choose how the public PDF is produced.'}));

    const labelField = document.createElement('label'); labelField.className='program-download-field'; labelField.dataset.configPath='program.download.label';
    const labelCaption = document.createElement('span'); labelCaption.textContent='Button label';
    const labelInput = document.createElement('input'); labelInput.type='text'; labelInput.value=cfg.label || 'Download program PDF';
    labelField.append(labelCaption,labelInput,Object.assign(document.createElement('small'),{textContent:'Text shown on the Program page.'}));

    grid.append(modeField,labelField); shell.append(grid);

    const generated = div('program-download-mode-panel'); generated.dataset.mode='generated';
    const generatedField = document.createElement('label'); generatedField.className='program-download-field'; generatedField.dataset.configPath='program.download.generated_filename';
    const generatedCaption = document.createElement('span'); generatedCaption.textContent='Generated filename';
    const generatedInput = document.createElement('input'); generatedInput.type='text'; generatedInput.value=cfg.generated_filename || ((state.projectName||'conference')+'-program.pdf');
    generatedField.append(generatedCaption,generatedInput,Object.assign(document.createElement('small'),{textContent:'Filename offered when the PDF is generated from program.csv.'}));
    const generatedNote = div('program-download-mode-note'); generatedNote.innerHTML='<b>Generated PDF</b><span>The website builds the document from <code>'+escapeHtml(state.program.path||'program.csv')+'</code> using the Program PDF settings below.</span>';
    generated.append(generatedNote,generatedField);

    const local = div('program-download-mode-panel'); local.dataset.mode='local';
    const localGrid = div('program-download-local-grid');
    const fileField = document.createElement('label'); fileField.className='program-download-field program-download-file-field'; fileField.dataset.configPath='program.download.local_file';
    const fileCaption = document.createElement('span'); fileCaption.textContent='Local PDF';
    const fileSelect = document.createElement('select');
    const pdfs = programPdfAssets();
    const current = String(cfg.local_file || '');
    const empty = document.createElement('option'); empty.value=''; empty.textContent='Choose a PDF…'; fileSelect.append(empty);
    pdfs.forEach((asset)=>{const option=document.createElement('option');option.value=asset.path;option.textContent=asset.path;fileSelect.append(option);});
    if(current && !pdfs.some((asset)=>asset.path===current)){const option=document.createElement('option');option.value=current;option.textContent=current+' (configured)';fileSelect.append(option);}
    fileSelect.value=current;
    fileField.append(fileCaption,fileSelect,Object.assign(document.createElement('small'),{textContent:'PDF stored in assets/documents/.'}));

    const localNameField = document.createElement('label'); localNameField.className='program-download-field'; localNameField.dataset.configPath='program.download.local_filename';
    const localNameCaption = document.createElement('span'); localNameCaption.textContent='Download filename';
    const localNameInput = document.createElement('input'); localNameInput.type='text'; localNameInput.value=cfg.local_filename || fileNameFromPath(current) || ((state.projectName||'conference')+'-program.pdf');
    localNameField.append(localNameCaption,localNameInput,Object.assign(document.createElement('small'),{textContent:'Filename offered to visitors for the local PDF.'}));
    localGrid.append(fileField,localNameField); local.append(localGrid);
    const localActions=div('inline-actions program-download-actions');
    const upload=button(current ? 'Replace / upload PDF' : 'Upload PDF','button ghost');
    const assets=button('Open Assets','button ghost');
    const localStatus=div('program-download-status', current ? 'Selected: '+current : 'No local PDF selected.');
    localActions.append(upload,assets,localStatus);local.append(localActions);

    shell.append(generated,local);

    function refreshMode(){
      const isLocal=mode.value==='local';
      generated.hidden=isLocal;
      local.hidden=!isLocal;
      shell.classList.toggle('is-disabled',!enabled.checked);
    }
    enabled.addEventListener('change',()=>{cfg.enabled=enabled.checked;syncStructuredYaml('program.download_enabled_changed',{enabled:cfg.enabled});refreshMode();});
    mode.addEventListener('change',()=>{cfg.mode=mode.value;syncStructuredYaml('program.download_mode_changed',{mode:cfg.mode});refreshMode();});
    labelInput.addEventListener('input',()=>{cfg.label=labelInput.value;syncStructuredYaml('program.download_label_changed',{});});
    generatedInput.addEventListener('input',()=>{cfg.generated_filename=generatedInput.value;syncStructuredYaml('program.download_filename_changed',{mode:'generated'});});
    localNameInput.addEventListener('input',()=>{cfg.local_filename=localNameInput.value;syncStructuredYaml('program.download_filename_changed',{mode:'local'});});
    fileSelect.addEventListener('change',()=>{
      cfg.local_file=fileSelect.value;
      if(fileSelect.value && (!cfg.local_filename || cfg.local_filename==='program.pdf')){cfg.local_filename=fileNameFromPath(fileSelect.value);localNameInput.value=cfg.local_filename;}
      syncStructuredYaml('program.download_local_changed',{path:fileSelect.value});
      localStatus.textContent=fileSelect.value?'Selected: '+fileSelect.value:'No local PDF selected.';
      upload.textContent=fileSelect.value?'Replace / upload PDF':'Upload PDF';
    });
    upload.addEventListener('click',()=>chooseAndUploadProgramPdf(()=>renderSettingsSection()));
    assets.addEventListener('click',()=>switchView('assets'));
    refreshMode();
    return shell;
  }

  function renderSettingsExperience(section) {
    if (section === 'program') return renderProgramDownloadSettings();
    if (section !== 'appearance' || !state.config.appearance) return null;
    const cfg=state.config.appearance, themes=Array.isArray(cfg.themes)?cfg.themes:[], palettes=Array.isArray(cfg.palettes)?cfg.palettes:[];
    const shell=div('settings-experience');
    const head=div('settings-experience-head'); head.innerHTML='<div><div class="eyebrow">Quick appearance</div><h3>Theme & palette</h3><p>Choose the active appearance visually. Advanced definitions remain editable below.</p></div>';
    shell.append(head);
    const controls=div('appearance-quick-grid');
    const themeLabel=document.createElement('label'); themeLabel.innerHTML='<span>Active theme</span>'; const themeSelect=selectControl(themes.map(t=>({value:t.id,label:t.label||humanizeKey(t.id)})),cfg.default_theme,(v)=>{cfg.default_theme=v;syncStructuredYaml('appearance.theme_changed',{theme:v});renderSettingsSection();}); themeLabel.append(themeSelect);
    const paletteLabel=document.createElement('label'); paletteLabel.innerHTML='<span>Active palette</span>'; const paletteSelect=selectControl(palettes.map(t=>({value:t.id,label:t.label||humanizeKey(t.id)})),cfg.default_palette,(v)=>{cfg.default_palette=v;syncStructuredYaml('appearance.palette_changed',{palette:v});renderSettingsSection();}); paletteLabel.append(paletteSelect);
    const rememberLabel=document.createElement('label'); rememberLabel.className='appearance-toggle'; const remember=document.createElement('input'); remember.type='checkbox'; remember.checked=cfg.remember_preferences!==false; remember.addEventListener('change',()=>{cfg.remember_preferences=remember.checked;syncStructuredYaml('appearance.remember_changed',{value:remember.checked});}); const rememberText=document.createElement('span'); rememberText.innerHTML='<b>Remember visitor choice</b><small>Keep theme/palette selection between pages.</small>'; rememberLabel.append(remember,rememberText);
    controls.append(themeLabel,paletteLabel,rememberLabel); shell.append(controls);
    const themeCards=div('appearance-choice-grid'); themes.forEach((theme)=>{const card=button('', 'appearance-choice'+(theme.id===cfg.default_theme?' active':''));card.title='Use '+(theme.label||theme.id);const sw=div('appearance-theme-swatch');sw.style.background=theme.bg||'#111';sw.style.borderColor=theme.border_light||theme.border||'#444';const inner=div('appearance-theme-inner');inner.style.background=theme.bg_card||theme.bg_alt||'#222';inner.style.color=theme.text_heading||theme.text||'#fff';inner.textContent='Aa';sw.append(inner);const txt=div('appearance-choice-text');txt.innerHTML='<b>'+escapeHtml(theme.label||theme.id)+'</b><small>'+escapeHtml(theme.color_scheme||'theme')+'</small>';card.append(sw,txt);card.addEventListener('click',()=>{cfg.default_theme=theme.id;syncStructuredYaml('appearance.theme_changed',{theme:theme.id});renderSettingsSection();});themeCards.append(card);});
    shell.append(themeCards);
    const paletteCards=div('palette-choice-grid');palettes.forEach((pal)=>{const card=button('', 'palette-choice'+(pal.id===cfg.default_palette?' active':''));const sw=div('palette-swatch');const a=div('');a.style.background=pal.primary||'#888';const b=div('');b.style.background=pal.secondary||'#555';sw.append(a,b);const txt=div('appearance-choice-text');txt.innerHTML='<b>'+escapeHtml(pal.label||pal.id)+'</b><small>'+escapeHtml(pal.id)+'</small>';card.append(sw,txt);card.addEventListener('click',()=>{cfg.default_palette=pal.id;syncStructuredYaml('appearance.palette_changed',{palette:pal.id});renderSettingsSection();});paletteCards.append(card);});shell.append(paletteCards);
    return shell;
  }

  function renderSectionYamlEditor() {
    if (!state.config || !state.activeSettingsSection || !els.sectionYamlEditor) return;
    const wrapper = Object.create(null);
    wrapper[state.activeSettingsSection] = conferenceSectionForStorage(state.activeSettingsSection);
    els.sectionYamlEditor.value = window.YamlLite.stringify(wrapper);
    state.sectionYamlDirty = false;
    setSectionYamlStatus('valid', 'Section YAML synchronized');
  }

  function setSectionYamlStatus(kind, text) {
    if (!els.sectionYamlStatus) return;
    els.sectionYamlStatus.className = 'validation-bar ' + (kind || 'neutral');
    els.sectionYamlStatus.textContent = text;
  }

  function applySectionYamlFromEditor() {
    if (!state.config || !state.activeSettingsSection || !els.sectionYamlEditor) return false;
    try {
      const parsed = window.YamlLite.parse(els.sectionYamlEditor.value || '');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Section YAML must be a mapping');
      if (!Object.prototype.hasOwnProperty.call(parsed, state.activeSettingsSection)) {
        throw new Error('Expected top-level key "' + state.activeSettingsSection + '"');
      }
      state.config[state.activeSettingsSection] = parsed[state.activeSettingsSection];
      if (state.activeSettingsSection === 'registration') attachRegformConfig(state.config, state.regformConfig);
      state.sectionYamlDirty = false;
      syncStructuredYaml('setting.section_yaml_applied', { section: state.activeSettingsSection });
      renderSettingsSection();
      renderSpreadsheet('program');
      setSectionYamlStatus('valid', 'Section YAML applied');
      toast('Applied YAML section: ' + state.activeSettingsSection, 'success');
      return true;
    } catch (error) {
      setSectionYamlStatus('invalid', 'Section YAML error · ' + error.message);
      log('error', 'setting.section_yaml_failed', { section: state.activeSettingsSection, message: error.message });
      toast('Section YAML error: ' + error.message, 'error');
      return false;
    }
  }

  function renderYamlNode(value, path, label, depth) {
    if (Array.isArray(value)) return renderYamlArray(value, path, label, depth);
    if (value && typeof value === 'object') {
      const details = document.createElement('details'); details.className = 'yaml-node'; details.dataset.configPath = (path || []).map(String).join('.'); details.open = depth < 2 && !['themes','palettes','images','items'].includes(String(label||'').toLowerCase());
      const summary = document.createElement('summary');
      const left = document.createElement('span'); left.textContent = depth === 0 ? humanizeKey(label) : humanizeKey(label);
      const right = document.createElement('small'); right.textContent = Object.keys(value).length + ' fields';
      summary.append(left, right);
      const body = div('yaml-node-body');
      Object.keys(value).forEach((key) => body.append(renderYamlNode(value[key], path.concat(key), key, depth + 1)));
      details.append(summary, body);
      return details;
    }
    return renderYamlScalar(value, path, label);
  }

  function yamlSelectOptions(path,label,value){const joined=path.map(String).join('.'),key=String(label||'').toLowerCase();if(joined==='appearance.default_theme')return (getConfig('appearance.themes',[])||[]).map(x=>({value:x.id,label:x.label||humanizeKey(x.id)}));if(joined==='appearance.default_palette')return (getConfig('appearance.palettes',[])||[]).map(x=>({value:x.id,label:x.label||humanizeKey(x.id)}));if(key==='color_scheme')return ['light','dark'];if(joined==='runtime.log_level'||joined==='runtime.debug_log_level')return ['debug','info','warn','error'];if(joined==='site.language')return [{value:'en',label:'English'},{value:'it',label:'Italiano'},{value:'fr',label:'Français'},{value:'de',label:'Deutsch'},{value:'es',label:'Español'}];return null;}

  function isIsoDateField(path,label,value){const key=String(label||'').toLowerCase(),joined=path.map(String).join('.').toLowerCase();return /^\d{4}-\d{2}-\d{2}$/.test(String(value||''))||/(^|_)(start_date|end_date|deadline|date)$/.test(key)||/(start_date|end_date|deadline)$/.test(joined);}

  function renderYamlScalar(value, path, label) {
    const isLong = typeof value === 'string' && (value.length > 90 || value.includes('\n'));
    const isAsset = typeof value === 'string' && isAssetReferenceField(path, label, value);
    const wrap = div('yaml-field' + (isLong ? ' long' : '') + (isAsset ? ' asset-aware' : ''));
    wrap.dataset.configPath = path.map(String).join('.');
    const lab = document.createElement('label');
    lab.textContent = humanizeKey(label);
    const hint = document.createElement('small'); hint.textContent = displayConfigPath(path); lab.append(hint);
    let control;
    const selectOptions=yamlSelectOptions(path,label,value);
    if (typeof value === 'boolean') {
      const toggle=div('yaml-toggle');control=document.createElement('input');control.type='checkbox';control.checked=value;const txt=document.createElement('span');txt.textContent=value?'Enabled':'Disabled';control.addEventListener('change',()=>{txt.textContent=control.checked?'Enabled':'Disabled';updateStructuredYaml(path,control.checked);});toggle.append(control,txt);control._yamlWrapper=toggle;
    } else if (selectOptions && selectOptions.length) {
      control=selectControl(selectOptions,value,(v)=>updateStructuredYaml(path,v));
    } else if (typeof value === 'number') {
      control = document.createElement('input'); control.type = 'number'; control.step = 'any'; control.value = String(value);
      control.addEventListener('change', () => updateStructuredYaml(path, control.value === '' ? 0 : Number(control.value)));
    } else if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) {
      const box=div('yaml-color-control');const picker=document.createElement('input');picker.type='color';picker.value=value;control=document.createElement('input');control.type='text';control.value=value;const apply=(v)=>{control.value=v;picker.value=/^#[0-9a-f]{6}$/i.test(v)?v:picker.value;updateStructuredYaml(path,v);};picker.addEventListener('input',()=>apply(picker.value));control.addEventListener('input',()=>apply(control.value));box.append(picker,control);control._yamlWrapper=box;
    } else if (isLong) {
      control = document.createElement('textarea'); control.rows = Math.min(8, Math.max(3, value.split('\n').length)); control.value = value;
      control.addEventListener('change', () => updateStructuredYaml(path, control.value));
    } else {
      control = document.createElement('input');
      const key = String(label || '').toLowerCase();
      control.type = isIsoDateField(path,label,value) ? 'date' : key.includes('email') ? 'email' : (key.includes('url') || key.includes('href') || key.includes('website') ? 'url' : 'text');
      control.value = value == null ? '' : String(value);
      control.addEventListener('input', () => updateStructuredYaml(path, control.value));
    }
    wrap.append(lab);
    const renderedControl=control._yamlWrapper||control;
    if (!isAsset) { wrap.append(renderedControl); return wrap; }

    const currentValue = String(value || '');
    if (isImageAssetReferenceField(path, label, currentValue)) {
      wrap.classList.add('image-aware');
      wrap.append(renderYamlImageEditor(path, label, currentValue, control));
      return wrap;
    }

    const assetBox = div('yaml-asset-field');
    assetBox.append(renderedControl);
    const chooserRow = div('yaml-asset-row');
    const select = document.createElement('select');
    const manual = document.createElement('option');
    manual.value = ''; manual.textContent = currentValue ? 'Choose another asset…' : 'Choose an asset…';
    select.append(manual);
    const relevant = assetsForYamlField(path, label, currentValue);
    relevant.forEach((asset) => { const option=document.createElement('option');option.value=asset.path;option.textContent=asset.path;select.append(option); });
    select.addEventListener('change', () => { if (!select.value) return; control.value=select.value;updateStructuredYaml(path,select.value);refreshYamlMutationView(path); });
    const upload = button(currentValue && state.assets.some((asset) => asset.path === currentValue) ? 'Replace / upload' : 'Upload asset', 'button ghost');
    upload.addEventListener('click', () => uploadAssetForYamlField(path, label, currentValue));
    chooserRow.append(select, upload); assetBox.append(chooserRow);
    const preview=renderYamlAssetPreview(currentValue);if(preview)assetBox.append(preview);wrap.append(assetBox);return wrap;
  }

  function isAssetReferenceField(path, label, value) {
    const key = String(label || '').toLowerCase();
    const joined = path.map((part) => String(part).toLowerCase()).join('.');
    if (String(value || '').startsWith('assets/')) return true;
    if (/(logo|image|photo|picture|thumbnail|banner|background|favicon|icon|poster|asset|document|brochure|pdf|file|attachment|download|map_image|hero_image|src)$/.test(key)) return true;
    return /(assets|gallery|branding|venue|accommodation|sponsors|social).*(logo|image|photo|src|file|document|pdf)/.test(joined);
  }

  function isImageAssetReferenceField(path, label, value) {
    const key = String(label || '').toLowerCase();
    const joined = path.map((part) => String(part).toLowerCase()).join('.');
    const ext = extensionOf(value);
    if (IMAGE_EXTENSIONS.has(ext)) return true;
    if (/(logo|image|photo|picture|thumbnail|banner|background|favicon|icon|poster|map_image|hero_image|src)$/.test(key)) return true;
    return /(assets|gallery|branding|venue|accommodation|sponsors|social|school|hero).*(logo|image|photo|picture|src)/.test(joined);
  }

  function setYamlAssetValue(path, value, reason) {
    updateStructuredYaml(path, value || '');
    log('debug', reason || 'setting.asset_selected', { path: displayConfigPath(path), asset: value || '' });
    refreshYamlMutationView(path);
  }

  function renderYamlImageEditor(path, label, currentValue, pathControl) {
    const editor = div('yaml-image-editor' + (currentValue ? ' has-image' : ' empty'));
    const current = div('yaml-image-current');
    const visual = div('yaml-image-visual');
    const asset = state.assets.find((item) => item.path === currentValue);
    const externalUrl = /^https?:\/\//i.test(currentValue) ? currentValue : '';
    const previewUrl = asset ? assetPreviewUrl(currentValue) : externalUrl;
    if (currentValue && previewUrl) {
      const img = document.createElement('img');
      img.src = previewUrl;
      img.alt = humanizeKey(label) + ' preview';
      img.loading = 'lazy';
      img.addEventListener('click', () => openYamlImagePreview(currentValue, label));
      visual.append(img);
    } else {
      const empty = div('yaml-image-empty');
      if (currentValue) empty.innerHTML = '<b>Image not found</b><span>The configured path is not currently available in project Assets.</span>';
      else empty.innerHTML = '<b>No image selected</b><span>Choose an existing project image or upload a new one.</span>';
      visual.append(empty);
    }
    const info = div('yaml-image-info');
    const title = div('yaml-image-title', currentValue ? fileNameFromPath(currentValue) : 'No image');
    const pathText = div('yaml-image-path', currentValue || '—');
    const status = div('yaml-image-status', currentValue ? (asset ? 'Project asset' : 'Configured path · file not currently loaded') : 'Optional / empty');
    info.append(title, pathText, status);
    current.append(visual, info);

    const actions = div('yaml-image-actions');
    const choose = button(currentValue ? 'Change image' : 'Choose image', 'button primary');
    const upload = button(currentValue && asset ? 'Replace image' : 'Upload image', 'button');
    const view = button('View', 'button ghost');
    const clear = button('Remove', 'button ghost danger');
    view.disabled = !currentValue;
    clear.disabled = !currentValue;
    choose.addEventListener('click', () => openYamlAssetPicker(path, label));
    upload.addEventListener('click', () => uploadAssetForYamlField(path, label, String(getAtPath(state.config, path) || '')));
    view.addEventListener('click', () => openYamlImagePreview(String(getAtPath(state.config, path) || ''), label));
    clear.addEventListener('click', async () => {
      if (!String(getAtPath(state.config, path) || '')) return;
      const ok = await totemConfirm('Remove image', 'Remove this image from the section? The asset file itself will stay in Assets.', 'Remove', { danger:true });
      if (!ok) return;
      setYamlAssetValue(path, '', 'setting.image_cleared');
    });
    actions.append(choose, upload, view, clear);

    const advanced = document.createElement('details');
    advanced.className = 'yaml-image-advanced';
    const summary = document.createElement('summary'); summary.textContent = 'Advanced · asset path';
    const holder = div('yaml-image-path-editor');
    pathControl.value = currentValue;
    pathControl.addEventListener('change', () => refreshYamlMutationView(path));
    holder.append(pathControl); advanced.append(summary, holder);

    editor.append(current, actions, advanced);
    return editor;
  }

  function openYamlImagePreview(path, label) {
    if (!path) return;
    els.modalTitle.textContent = 'Image preview · ' + humanizeKey(label || 'image');
    els.modalBody.replaceChildren();
    const shell = div('yaml-image-modal-preview');
    const asset = state.assets.find((item) => item.path === path);
    const previewUrl = asset ? assetPreviewUrl(path) : (/^https?:\/\//i.test(path) ? path : '');
    if (previewUrl) { const img = document.createElement('img'); img.src = previewUrl; img.alt = fileNameFromPath(path); shell.append(img); }
    else shell.append(div('sheet-empty','This image path is configured, but the file is not currently available in Assets.'));
    const meta = div('yaml-image-modal-meta'); meta.append(div('', fileNameFromPath(path)), div('yaml-image-path', path));
    shell.append(meta); els.modalBody.append(shell); els.modalBackdrop.classList.remove('hidden');
  }

  function openYamlAssetPicker(path, label) {
    const currentValue = String(getAtPath(state.config, path) || '');
    const allImages = assetsForYamlField(path, label, currentValue).filter((asset) => IMAGE_EXTENSIONS.has(extensionOf(asset.path)));
    els.modalTitle.textContent = 'Choose image · ' + humanizeKey(label || 'image');
    els.modalBody.replaceChildren();

    const top = div('yaml-image-picker-top');
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search project images…';
    const upload = button('Upload new image', 'button primary');
    const clear = button('No image', 'button ghost');
    upload.addEventListener('click', () => { closeModal(); uploadAssetForYamlField(path, label, currentValue); });
    clear.addEventListener('click', () => { closeModal(); setYamlAssetValue(path, '', 'setting.image_cleared'); });
    top.append(search, upload, clear); els.modalBody.append(top);

    const note = div('yaml-image-picker-note', allImages.length ? allImages.length + ' project images available. Click a thumbnail to use it.' : 'No project images found yet. Upload one to add it.');
    els.modalBody.append(note);
    const grid = div('yaml-image-picker-grid'); els.modalBody.append(grid);

    const render = () => {
      grid.replaceChildren();
      const q = search.value.trim().toLowerCase();
      const filtered = allImages.filter((asset) => !q || asset.path.toLowerCase().includes(q));
      filtered.forEach((asset) => {
        const card = button('', 'yaml-image-picker-card' + (asset.path === currentValue ? ' active' : ''));
        const image = document.createElement('img'); image.src = assetPreviewUrl(asset.path); image.alt = fileNameFromPath(asset.path); image.loading='lazy';
        const copy = div('yaml-image-picker-copy');
        copy.append(div('yaml-image-picker-name', fileNameFromPath(asset.path)), div('yaml-image-picker-path', asset.path));
        card.append(image, copy);
        card.addEventListener('click', () => { closeModal(); setYamlAssetValue(path, asset.path, 'setting.image_selected'); toast('Image selected: ' + asset.path, 'success'); });
        grid.append(card);
      });
      if (!filtered.length && allImages.length) grid.append(div('sheet-empty','No images match your search.'));
    };
    search.addEventListener('input', render); render();
    els.modalBackdrop.classList.remove('hidden');
    window.setTimeout(() => search.focus(), 0);
  }

  function imagePathFromYamlItem(item) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return '';
    return String(item.src || item.image || item.photo || item.logo || '');
  }

  function isYamlImageCollection(array, path, label) {
    const joined = ((path || []).map(String).join('.') + '.' + String(label || '')).toLowerCase();
    if (/(^|\.)(images|gallery)$/.test(joined)) return true;
    return Array.isArray(array) && array.some((item) => IMAGE_EXTENSIONS.has(extensionOf(imagePathFromYamlItem(item))));
  }

  function assetsForYamlField(path, label, currentValue) {
    const key = (path.join('.') + '.' + label).toLowerCase();
    const wantsImage = /(image|photo|picture|thumbnail|banner|background|favicon|icon|poster|logo|src|gallery)/.test(key) || PREVIEWABLE_EXTENSIONS.has(extensionOf(currentValue));
    const wantsDocument = /(document|brochure|pdf|file)/.test(key) && !wantsImage;
    return state.assets.filter((asset) => {
      const ext = extensionOf(asset.path);
      if (wantsImage) return IMAGE_EXTENSIONS.has(ext);
      if (wantsDocument) return !IMAGE_EXTENSIONS.has(ext);
      return true;
    });
  }

  function renderYamlAssetPreview(path) {
    if (!path) return null;
    const asset = state.assets.find((item) => item.path === path);
    const preview = div('yaml-asset-preview');
    if (asset && PREVIEWABLE_EXTENSIONS.has(extensionOf(path))) {
      const img = document.createElement('img'); img.src = assetPreviewUrl(path); img.alt = fileNameFromPath(path); preview.append(img);
    } else {
      const badge = div('yaml-asset-file', asset ? extensionOf(path).toUpperCase() || 'FILE' : 'PATH');
      const text = div('yaml-asset-path', path);
      preview.append(badge, text);
    }
    return preview;
  }

  function assetDirectoryForField(path, label, currentValue) {
    if (currentValue && currentValue.startsWith('assets/') && currentValue.includes('/')) return currentValue.slice(0, currentValue.lastIndexOf('/') + 1);
    const joined = (path.join('.') + '.' + label).toLowerCase();
    if (/(logo|favicon|branding|icon)/.test(joined)) return 'assets/branding/';
    if (/people|speaker|person/.test(joined)) return 'assets/people/';
    if (/venue/.test(joined)) return 'assets/venue/';
    if (/accommodation|hotel/.test(joined)) return 'assets/accommodation/';
    if (/sponsor/.test(joined)) return 'assets/sponsors/';
    if (/social/.test(joined)) return 'assets/social/';
    if (/document|brochure|pdf/.test(joined)) return 'assets/documents/';
    return 'assets/images/';
  }

  async function uploadAssetForYamlField(path, label, currentValue) {
    const input = document.createElement('input');
    input.type = 'file'; input.hidden = true;
    const joined = (path.join('.') + '.' + label).toLowerCase();
    if (/(image|photo|picture|thumbnail|banner|background|favicon|icon|poster|logo|src|gallery)/.test(joined)) input.accept = 'image/*';
    document.body.append(input);
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0]; input.remove(); if (!file) return;
      try {
        const directory = assetDirectoryForField(path, label, currentValue);
        let target;
        const currentExt = extensionOf(currentValue);
        const newExt = extensionOf(file.name);
        if (currentValue && currentValue.startsWith('assets/') && state.assets.some((asset) => asset.path === currentValue) && currentExt && currentExt === newExt) target = currentValue;
        else target = directory + file.name;
        const result = await writeProjectBlob(target, file);
        setAtPath(state.config, path, target);
        if (isRegformPath(path)) syncRegformSettings('regform.asset_uploaded', { path: displayConfigPath(path), asset: target, mode: result });
        else syncStructuredYaml('setting.asset_uploaded', { path: path.join('.'), asset: target, mode: result });
        await loadAssets();
        refreshYamlMutationView(path);
        toast((result === 'saved' ? 'Asset saved: ' : 'Asset staged: ') + target, 'success');
      } catch (error) {
        log('error', 'setting.asset_upload_failed', { path: path.join('.'), message: error.message });
        toast('Could not save asset: ' + error.message, 'error');
      }
    });
    input.click();
  }

  function blankYamlArrayItem(path, array) {
    if (array && array.length) return blankLike(array[0]);
    const joined = (path || []).map(String).join('.');
    if (joined === 'registration.form.sections') {
      return { id: 'new_section', title: 'New section', fields: [] };
    }
    if (/^registration\.form\.sections\.\d+\.fields$/.test(joined)) {
      return { name: 'new_field', label: 'New field', type: 'text', required: false };
    }
    if (/(?:^|\.)(?:images|gallery)$/.test(joined)) return { src:'', alt:'', caption:'' };
    if (/\.options$/.test(joined)) return '';
    if (joined === 'registration.form.backend.trusted_proxies') return '';
    return '';
  }

  function refreshYamlMutationView(path) {
    if (isRegformPath(path)) {
      const openPaths = new Set();
      if (els.regformForm) {
        els.regformForm.querySelectorAll('details.yaml-node[open][data-config-path]').forEach((node) => openPaths.add(node.dataset.configPath));
      }
      openPaths.add((path || []).map(String).join('.'));
      renderRegformPage();
      if (els.regformForm) {
        els.regformForm.querySelectorAll('details.yaml-node[data-config-path]').forEach((node) => {
          if (openPaths.has(node.dataset.configPath)) node.open = true;
        });
      }
      return;
    }
    renderSettingsSection();
  }

  function renderYamlArray(array, path, label, depth) {
    const imageCollection = isYamlImageCollection(array, path, label);
    const details = document.createElement('details'); details.className = 'yaml-node' + (imageCollection ? ' yaml-image-collection' : ''); details.dataset.configPath = (path || []).map(String).join('.'); details.open = imageCollection ? depth < 4 : (depth < 2 && !['themes','palettes','images','items'].includes(String(label||'').toLowerCase()));
    const summary = document.createElement('summary');
    const left = document.createElement('span'); left.textContent = humanizeKey(label);
    const right = document.createElement('small'); right.textContent = array.length + ' items';
    summary.append(left, right);
    const body = div('yaml-node-body yaml-array');
    array.forEach((item, index) => {
      const itemBox = div('yaml-array-item');
      const head = div('yaml-array-item-head');
      if (imageCollection) {
        const imagePath = imagePathFromYamlItem(item);
        const identity = div('yaml-gallery-item-identity');
        if (imagePath) { const imageAsset=state.assets.find((asset)=>asset.path===imagePath); if(imageAsset){const thumb=document.createElement('img');thumb.src=assetPreviewUrl(imagePath);thumb.alt='Image '+(index+1);identity.append(thumb);} }
        const copy=div('');copy.append(div('', (item && item.caption) ? String(item.caption) : 'Image ' + (index + 1)), div('yaml-gallery-item-path', imagePath || 'No image selected'));identity.append(copy);head.append(identity);
      } else head.append(div('', 'Item ' + (index + 1)));
      const actions = div('yaml-array-actions');
      const up = button('↑', 'yaml-mini-btn'); const down = button('↓', 'yaml-mini-btn');
      const clone = button('Duplicate', 'yaml-mini-btn'); const remove = button('Delete', 'yaml-mini-btn');
      up.disabled = index === 0; down.disabled = index === array.length - 1;
      up.addEventListener('click', () => mutateYamlArray(path, (arr) => { const x = arr.splice(index,1)[0]; arr.splice(index-1,0,x); }));
      down.addEventListener('click', () => mutateYamlArray(path, (arr) => { const x = arr.splice(index,1)[0]; arr.splice(index+1,0,x); }));
      clone.addEventListener('click', () => mutateYamlArray(path, (arr) => arr.splice(index + 1, 0, deepClone(arr[index]))));
      remove.addEventListener('click', () => mutateYamlArray(path, (arr) => arr.splice(index, 1)));
      actions.append(up, down, clone, remove); head.append(actions); itemBox.append(head);
      if (item && typeof item === 'object') {
        if (Array.isArray(item)) itemBox.append(renderYamlArray(item, path.concat(index), 'values', depth + 1));
        else Object.keys(item).forEach((key) => itemBox.append(renderYamlNode(item[key], path.concat(index, key), key, depth + 1)));
      } else itemBox.append(renderYamlScalar(item, path.concat(index), 'value'));
      body.append(itemBox);
    });
    const add = button(imageCollection ? '+ Add image' : '+ Add item', 'button ghost yaml-add');
    add.addEventListener('click', () => mutateYamlArray(path, (arr) => arr.push(blankYamlArrayItem(path, arr))));
    body.append(add); details.append(summary, body); return details;
  }

  function updateStructuredYaml(path, value) {
    try {
      setAtPath(state.config, path, value);
      if (isRegformPath(path)) syncRegformSettings('regform.setting_changed', { path: path.slice(2).join('.') });
      else syncStructuredYaml('setting.changed', { path: path.join('.') });
      if (path.join('.') === 'conference.start_date' || path.join('.') === 'conference.end_date') renderSpreadsheet('program');
    } catch (error) {
      log('error', 'setting.change_failed', { path: path.join('.'), message: error.message });
      toast('Could not update YAML: ' + error.message, 'error');
    }
  }

  function mutateYamlArray(path, mutator) {
    try {
      const array = getAtPath(state.config, path);
      if (!Array.isArray(array)) throw new Error('Expected an array at ' + displayConfigPath(path));
      mutator(array);
      if (isRegformPath(path)) syncRegformSettings('regform.array_changed', { path: path.slice(2).join('.'), items: array.length });
      else syncStructuredYaml('setting.array_changed', { path: path.join('.'), items: array.length });
      refreshYamlMutationView(path);
    } catch (error) {
      log('error', 'setting.array_change_failed', { path: displayConfigPath(path), message: error.message, stack: error.stack || '' });
      toast('Could not update ' + displayConfigPath(path) + ': ' + error.message, 'error');
    }
  }

  function syncRegformSettings(eventName, details) {
    state.regformConfig = getConfig('registration.form', Object.create(null));
    state.regformYamlText = stringifyRegformSettings(state.regformConfig);
    state.regformDirty = true;
    if (state.activeView === 'overview') renderOverview();
    updateSaveState();
    log('debug', eventName, details);
  }

  function syncStructuredYaml(eventName, details) {
    state.yamlText = window.YamlLite.stringify(conferenceConfigForStorage());
    state.yamlDirty = true;
    state.sectionYamlDirty = false;
    els.yamlEditor.value = state.yamlText;
    setYamlStatus('valid', 'conference.yaml valid · structured changes not saved');
    if (state.activeSettingsSection) renderSectionYamlEditor();
    syncVisibleInlineYamlEditors();
    if (state.activeView === 'overview') renderOverview();
    updateSaveState();
    log('debug', eventName, details);
  }

  function syncVisibleInlineYamlEditors() {
    document.querySelectorAll('.section-yaml-editor[data-section]').forEach((textarea) => {
      const section = textarea.dataset.section || '';
      if (!section || state.sectionYamlDrafts.has(section) || !Object.prototype.hasOwnProperty.call(state.config, section)) return;
      const wrapper = Object.create(null);
      wrapper[section] = conferenceSectionForStorage(section);
      const next = window.YamlLite.stringify(wrapper);
      if (textarea.value !== next) textarea.value = next;
      const status = textarea.previousElementSibling;
      if (status && status.classList && status.classList.contains('validation-bar')) {
        status.className = 'validation-bar valid';
        status.textContent = 'Synchronized with visual fields';
      }
    });
  }

  function getAtPath(root, path) {
    let current = root;
    for (const key of path) current = current[key];
    return current;
  }

  function setAtPath(root, path, value) {
    let current = root;
    for (let i = 0; i < path.length - 1; i += 1) current = current[path[i]];
    current[path[path.length - 1]] = value;
  }

  function deepClone(value) {
    if (value == null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map(deepClone);
    const out = Object.create(null); Object.keys(value).forEach((key) => { out[key] = deepClone(value[key]); }); return out;
  }

  function blankLike(value) {
    if (value == null) return '';
    if (typeof value === 'boolean') return false;
    if (typeof value === 'number') return 0;
    if (typeof value === 'string') return '';
    if (Array.isArray(value)) return [];
    const out = Object.create(null); Object.keys(value || {}).forEach((key) => { out[key] = blankLike(value[key]); }); return out;
  }

  function humanizeKey(value) {
    return String(value || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
  }

  function validateYamlFromEditor() {
    try {
      const text = els.yamlEditor.value;
      const config = window.YamlLite.parse(text);
      if (config.registration && typeof config.registration === 'object' && Object.prototype.hasOwnProperty.call(config.registration, 'form')) {
        state.regformConfig = config.registration.form;
        state.regformYamlText = stringifyRegformSettings(state.regformConfig);
        state.regformDirty = true;
        delete config.registration.form;
        state.yamlText = window.YamlLite.stringify(config);
        els.yamlEditor.value = state.yamlText;
      } else state.yamlText = text;
      state.config = attachRegformConfig(config, state.regformConfig);
      setYamlStatus('valid', 'YAML valid');
      if (!Object.prototype.hasOwnProperty.call(state.config, state.activeSettingsSection)) state.activeSettingsSection = Object.keys(state.config)[0] || '';
      renderSettings();
      renderOverview();
      log('info', 'yaml.validated', { bytes: new Blob([text]).size });
      toast('YAML is valid.', 'success');
      return true;
    } catch (error) {
      setYamlStatus('invalid', 'YAML error · ' + error.message);
      log('error', 'yaml.validation_failed', { message: error.message });
      toast('YAML error: ' + error.message, 'error');
      return false;
    }
  }

  async function saveYamlFromEditor() {
    if (!validateYamlFromEditor()) return;
    await saveYaml();
  }

  async function saveYaml() {
    if (!state.config) return;
    try {
      if (state.sectionYamlDirty && !applySectionYamlFromEditor()) return;
      const parsed = window.YamlLite.parse(state.yamlText);
      if (parsed.registration && typeof parsed.registration === 'object') delete parsed.registration.form;
      state.yamlText = window.YamlLite.stringify(parsed);
      els.yamlEditor.value = state.yamlText;
      state.config = attachRegformConfig(parsed, state.regformConfig);
      const result = await writeProjectText('conference.yaml', state.yamlText.endsWith('\n') ? state.yamlText : state.yamlText + '\n');
      state.yamlDirty = false;
      setYamlStatus('valid', result === 'saved' ? 'YAML saved' : 'YAML staged for ZIP export');
      if (!Object.prototype.hasOwnProperty.call(state.config, state.activeSettingsSection)) state.activeSettingsSection = Object.keys(state.config)[0] || '';
      renderSettings();
      renderOverview();
      updateSaveState();
      log('info', 'yaml.' + result, { project: state.projectName });
      toast(result === 'saved' ? 'conference.yaml saved.' : 'conference.yaml staged for ZIP export.', 'success');
    } catch (error) {
      setYamlStatus('invalid', 'YAML not saved · ' + error.message);
      log('error', 'yaml.save_failed', { message: error.message });
      toast('Could not save YAML: ' + error.message, 'error');
      throw error;
    }
  }

  async function saveRegformSettings() {
    if (!state.config || !state.regformConfig) return false;
    const emailProblem = validateRegformEmailSettings(true);
    if (emailProblem) {
      switchView('regform');
      renderRegformPage();
      toast(emailProblem, 'error');
      return false;
    }
    try {
      state.regformYamlText = stringifyRegformSettings(state.regformConfig);
      const result = await writeProjectText('regform/settings.yaml', state.regformYamlText.endsWith('\n') ? state.regformYamlText : state.regformYamlText + '\n');
      state.regformDirty = false;
      updateSaveState();
      log('info', 'regform.settings_' + result, { project: state.projectName, path:'regform/settings.yaml' });
      toast(result === 'saved' ? 'regform/settings.yaml saved.' : 'regform/settings.yaml staged for ZIP export.', 'success');
      return true;
    } catch (error) {
      log('error', 'regform.settings_save_failed', { message:error.message });
      toast('Could not save regform/settings.yaml: ' + error.message, 'error');
      return false;
    }
  }

  function setYamlStatus(kind, text) {
    els.yamlStatus.className = 'validation-bar ' + kind;
    els.yamlStatus.textContent = text;
  }

  function renderNoProjectSheets() {
    [els.peopleEditor, els.programEditor, els.datesEditor, els.documentsEditor].forEach((container) => {
      container.replaceChildren();
      const empty = div('empty-state compact');
      const h2 = document.createElement('h2'); h2.textContent = 'No conference loaded';
      const p = document.createElement('p'); p.textContent = 'Open a project first.';
      empty.append(h2, p); container.append(empty);
    });
  }

  function renderSpreadsheet(kind) {
    if (kind === 'people') return renderPeopleSpreadsheet();
    return renderGenericSpreadsheet(kind);
  }

  function spreadsheetColumnWeight(kind, header) {
    const key=String(header||'').trim().toLowerCase();
    if(kind==='people') {
      const weights={
        '#':3,'photo':5,'first name':9,'last name':9,'category':8,'role':13,
        'affiliation':13,'country':7,'presentation title':20,'presentation type':9,'image':13,'visible':6,'row':10
      };
      return weights[key] || 8;
    }
    const weights={
      '#':2,'day':4,'date':9,'start time':5,'end time':5,'type':7,'title':17,
      'speaker':9,'affiliation':10,'chair':7,'location':7,'notes':8,'description':8,
      'visible':4,'row':6
    };
    if (Object.prototype.hasOwnProperty.call(weights,key)) return weights[key];
    if (/title/.test(key)) return 17;
    if (/notes|description/.test(key)) return 8;
    if (/speaker|presenter|name/.test(key)) return 9;
    if (/affiliation|institution/.test(key)) return 10;
    if (/date/.test(key)) return 9;
    if (/time/.test(key)) return 5;
    return 7;
  }

  function applySpreadsheetColumns(table, kind, headers) {
    const labels=['#'].concat(headers).concat(['Row']);
    const weights=labels.map((label)=>spreadsheetColumnWeight(kind,label));
    const total=weights.reduce((sum,value)=>sum+value,0)||1;
    const group=document.createElement('colgroup');
    labels.forEach((label,index)=>{
      const col=document.createElement('col');
      col.dataset.column=String(label);
      col.style.width=((weights[index]/total)*100).toFixed(3)+'%';
      group.append(col);
    });
    table.append(group);
  }

  function bindResponsiveSpreadsheet(shell, kind) {
    const previous=state.sheetObservers[kind];
    if(previous&&typeof previous.disconnect==='function')previous.disconnect();
    state.sheetObservers[kind]=null;
    shell.classList.remove('cards-mode');
    shell.dataset.layout='table';
  }

  function sheetImportControls(kind, toolbar, onImported) {
    const csvBtn = button(kind === 'people' ? 'Import CSV' : 'Import CSV', 'button ghost');
    const excelBtn = button(kind === 'people' ? 'Import Excel(s)' : 'Import Excel', 'button ghost');
    const exportBtn = button('Export CSV', 'button ghost');
    const exportExcelBtn = button('Export Excel', 'button ghost');
    const csvInput = document.createElement('input'); csvInput.type = 'file'; csvInput.accept = '.csv,text/csv'; csvInput.hidden = true;
    const excelInput = document.createElement('input'); excelInput.type = 'file'; excelInput.accept = '.xlsx,.xlsm,.xltx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; excelInput.hidden = true; excelInput.multiple = kind === 'people';
    csvBtn.addEventListener('click', () => csvInput.click());
    excelBtn.addEventListener('click', () => excelInput.click());
    csvInput.addEventListener('change', async () => { const file = csvInput.files && csvInput.files[0]; csvInput.value=''; if (file) await importSheetFile(kind, file, onImported); });
    excelInput.addEventListener('change', async () => { const files = Array.from(excelInput.files || []); excelInput.value=''; if (!files.length) return; if (kind === 'people') await importPeopleSpreadsheetFiles(files, onImported); else await importSheetFile(kind, files[0], onImported); });
    exportBtn.addEventListener('click', () => exportSheet(kind));
    exportExcelBtn.addEventListener('click', () => exportSheetXlsx(kind));
    toolbar.append(csvBtn, excelBtn, exportBtn, exportExcelBtn, csvInput, excelInput);
    if (kind === 'people') {
      const importBundleBtn=button('Import People bundle','button ghost');
      const exportBundleBtn=button('Export People bundle','button ghost');
      const bundleInput=document.createElement('input');bundleInput.type='file';bundleInput.accept='.zip,application/zip';bundleInput.hidden=true;
      importBundleBtn.addEventListener('click',()=>bundleInput.click());
      bundleInput.addEventListener('change',async()=>{const file=bundleInput.files&&bundleInput.files[0];bundleInput.value='';if(file)await importPeopleBundle(file,onImported);});
      exportBundleBtn.addEventListener('click',exportPeopleBundle);
      toolbar.append(importBundleBtn,exportBundleBtn,bundleInput);
    }
  }

  function zipReadU16(view, offset){return view.getUint16(offset,true);}
  function zipReadU32(view, offset){return view.getUint32(offset,true);}

  async function inflateZipRaw(bytes){
    if(typeof DecompressionStream!=='function')throw new Error('This browser cannot decompress this ZIP. Use a current browser or a bundle exported by this editor.');
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function readZipEntries(file){
    const bytes=new Uint8Array(await file.arrayBuffer()),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    let eocd=-1;for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--){if(zipReadU32(view,i)===0x06054B50){eocd=i;break;}}
    if(eocd<0)throw new Error('Invalid ZIP: end record not found');
    if(zipReadU16(view,eocd+4)!==0||zipReadU16(view,eocd+6)!==0||zipReadU16(view,eocd+8)!==zipReadU16(view,eocd+10))throw new Error('Multi-volume ZIP is unsupported');
    const count=zipReadU16(view,eocd+10);if(count===65535)throw new Error('ZIP64 is unsupported');let offset=zipReadU32(view,eocd+16);const decoder=new TextDecoder('utf-8'),entries=new Map();
    for(let i=0;i<count;i++){
      if(zipReadU32(view,offset)!==0x02014B50)throw new Error('Invalid ZIP central directory');
      if(zipReadU16(view,offset+8)&1)throw new Error('Encrypted ZIP is unsupported');
      const unixType=(zipReadU32(view,offset+38)>>>16)&0xf000;
      if(unixType&&unixType!==0x8000&&unixType!==0x4000)throw new Error('Special ZIP entry is unsupported');
      const expectedCrc=zipReadU32(view,offset+16),expectedSize=zipReadU32(view,offset+24);
      const method=zipReadU16(view,offset+10),compressedSize=zipReadU32(view,offset+20),nameLen=zipReadU16(view,offset+28),extraLen=zipReadU16(view,offset+30),commentLen=zipReadU16(view,offset+32),localOffset=zipReadU32(view,offset+42);
      const rawName=decoder.decode(bytes.subarray(offset+46,offset+46+nameLen)).replace(/\\/g,'/');offset+=46+nameLen+extraLen+commentLen;if(rawName.endsWith('/'))continue;
      if(!rawName||rawName.startsWith('/')||/^[A-Za-z]:\//.test(rawName)||rawName.includes('\u0000'))throw new Error('Unsafe ZIP path: '+rawName);
      const pathParts=rawName.split('/').filter(Boolean);if(!pathParts.length||pathParts.some((part)=>part==='.'||part==='..'))throw new Error('Unsafe ZIP path: '+rawName);
      const name=pathParts.join('/');
      if(entries.has(name))throw new Error('Duplicate ZIP path: '+name);
      if(zipReadU32(view,localOffset)!==0x04034B50)throw new Error('Invalid ZIP entry: '+name);
      const localNameLen=zipReadU16(view,localOffset+26),localExtraLen=zipReadU16(view,localOffset+28),start=localOffset+30+localNameLen+localExtraLen,compressed=bytes.subarray(start,start+compressedSize);
      if(start+compressedSize>bytes.length)throw new Error('Truncated ZIP entry: '+name);
      let data;if(method===0)data=compressed.slice();else if(method===8)data=await inflateZipRaw(compressed);else throw new Error('Unsupported ZIP compression method '+method+' for '+name);
      if(data.length!==expectedSize||window.ZipLite.crc32(data)!==expectedCrc)throw new Error('ZIP integrity check failed: '+name);
      entries.set(name,new Blob([data],{type:typeFromName(name)}));
    }
    return entries;
  }

  function stripBundleRoot(entries){
    const names=Array.from(entries.keys());if(!names.length)return entries;
    const firstParts=names.map((name)=>name.split('/')[0]);const root=firstParts.every((part)=>part===firstParts[0])?firstParts[0]:'';
    if(!root||names.some((name)=>!name.includes('/')))return entries;const out=new Map();entries.forEach((blob,name)=>out.set(name.split('/').slice(1).join('/'),blob));return out;
  }

  async function exportPeopleBundle(){
    if(!state.config)return;
    try{
      const headers=state.people.headers.slice();
      const csv=window.CsvUtil.serialize(state.people.rows,headers,{bom:false,eol:'\r\n',finalEol:true,protectFormulae:false});
      const xlsx=await window.XlsxLite.build(state.people.rows,headers,{sheetName:'People'});
      const referenced=Array.from(new Set(state.people.rows.map((row)=>String(row.Image||'').trim()).filter(Boolean)));
      const assetMap=new Map();
      state.assets.filter((item)=>item.path.startsWith('assets/people/')&&PREVIEWABLE_EXTENSIONS.has(extensionOf(item.path))).forEach((item)=>assetMap.set(item.path,item));
      referenced.forEach((path)=>{const asset=state.assets.find((item)=>item.path===path);if(asset)assetMap.set(path,asset);});
      const assets=Array.from(assetMap.values()).sort((a,b)=>a.path.localeCompare(b.path));
      const manifest={schema:'mifp.people.bundle.v1',project:state.projectName||'',exported_at:new Date().toISOString(),people_count:state.people.rows.length,headers,people_csv:'data/people.csv',people_xlsx:'data/people.xlsx',face_images:assets.map((asset)=>asset.path),referenced_face_images:referenced};
      const entries=[{path:'manifest.json',data:JSON.stringify(manifest,null,2)+'\n'},{path:'data/people.csv',data:csv},{path:'data/people.xlsx',data:xlsx}];
      assets.forEach((asset)=>entries.push({path:asset.path,data:asset.blob}));
      const blob=await window.ZipLite.createBlob(entries,{root:'people-bundle'});
      const filename=(state.projectName||'conference')+'-people-bundle.zip';downloadBlob(filename,blob);
      log('info','people.bundle_exported',{people:state.people.rows.length,images:assets.length,bytes:blob.size});toast('People bundle exported: '+state.people.rows.length+' people · '+assets.length+' face images.','success');
    }catch(error){log('error','people.bundle_export_failed',{message:error.message});toast('People bundle export failed: '+error.message,'error');}
  }

  async function importPeopleBundle(file,onImported){
    try{
      let entries=stripBundleRoot(await readZipEntries(file));
      let manifest=null;const manifestBlob=entries.get('manifest.json');if(manifestBlob){try{manifest=JSON.parse(await manifestBlob.text());}catch(_){}}
      const csvBlob=entries.get((manifest&&manifest.people_csv)||'data/people.csv')||entries.get('people.csv');
      if(!csvBlob)throw new Error('People bundle does not contain data/people.csv');
      const parsed=window.CsvUtil.parse(await csvBlob.text());const data=normalizePeopleData(preparePeopleImport(parsed));
      const imageNames=new Set((manifest&&Array.isArray(manifest.face_images)?manifest.face_images:[]).map(String));
      entries.forEach((blob,path)=>{if(path.startsWith('assets/people/')&&PREVIEWABLE_EXTENSIONS.has(extensionOf(path)))imageNames.add(path);});
      const bundleAssets=Array.from(imageNames).map((path)=>({path,blob:entries.get(path)})).filter((item)=>item.blob);
      const source={file:{name:file.name},ext:'zip',parsed,data,bundleAssets,bundleManifest:manifest};
      await openPeopleImportAssistant([source],onImported);
      log('info','people.bundle_analyzed',{file:file.name,people:data.rows.length,images:bundleAssets.length});
    }catch(error){log('error','people.bundle_import_failed',{file:file.name,message:error.message});toast('People bundle import failed: '+error.message,'error');}
  }

  async function parseSpreadsheetFile(file) {
    const ext = extensionOf(file.name);
    let parsed;
    if (ext === 'csv') parsed = window.CsvUtil.parse(await file.text());
    else if (['xlsx','xlsm','xltx'].includes(ext)) parsed = await window.XlsxLite.parse(file);
    else throw new Error('Supported spreadsheet formats are CSV and Excel XLSX/XLSM. Legacy .xls should be saved as .xlsx first.');
    if (!parsed.headers.length) throw new Error('The spreadsheet has no header row');
    return { ext, parsed };
  }

  function peopleIdentityKey(row, rule) {
    const first=normalizedHeaderKey(row && row['First Name']);
    const last=normalizedHeaderKey(row && row['Last Name']);
    if(!first||!last)return '';
    const name=first+'|'+last;
    if(rule==='strict'){
      const aff=normalizedLooseText(row && row.Affiliation);
      const country=normalizedHeaderKey(row && row.Country);
      if(aff)return name+'|aff:'+aff;
      if(country)return name+'|country:'+country;
      return '';
    }
    return name;
  }

  function peopleRowFingerprint(row) {
    return [normalizedHeaderKey(row&&row['First Name']),normalizedHeaderKey(row&&row['Last Name']),normalizedLooseText(row&&row.Affiliation),normalizedHeaderKey(row&&row.Country),normalizedLooseText(row&&row['Presentation Title'])].join('|');
  }

  function peopleDisplayName(row) {
    return [row && row['First Name'], row && row['Last Name']].filter(Boolean).join(' ').trim() || 'Unnamed person';
  }

  function normalizedLooseText(value) {
    return normalizedHeaderKey(value).replace(/\b(university|universita|universite|institute|institut|department|dept|the|of|di|de)\b/g,' ').replace(/\s+/g,' ').trim();
  }

  function editDistance(a,b) {
    a=String(a||'');b=String(b||'');if(a===b)return 0;if(!a.length)return b.length;if(!b.length)return a.length;
    const prev=Array.from({length:b.length+1},(_,i)=>i),cur=new Array(b.length+1);
    for(let i=1;i<=a.length;i++){cur[0]=i;for(let j=1;j<=b.length;j++){const cost=a[i-1]===b[j-1]?0:1;cur[j]=Math.min(cur[j-1]+1,prev[j]+1,prev[j-1]+cost);}for(let j=0;j<=b.length;j++)prev[j]=cur[j];}
    return prev[b.length];
  }

  function textSimilarity(a,b) {
    const A=normalizedLooseText(a),B=normalizedLooseText(b);if(!A||!B)return 0;if(A===B)return 1;return 1-(editDistance(A,B)/Math.max(A.length,B.length,1));
  }

  function similarPeopleScore(a,b) {
    const firstA=normalizedHeaderKey(a['First Name']),firstB=normalizedHeaderKey(b['First Name']),lastA=normalizedHeaderKey(a['Last Name']),lastB=normalizedHeaderKey(b['Last Name']);
    if(!firstA||!firstB||!lastA||!lastB)return 0;
    if(firstA===firstB&&lastA===lastB)return 1;
    if(firstA===lastB&&lastA===firstB)return .99;
    const first=textSimilarity(firstA,firstB),last=textSimilarity(lastA,lastB),full=textSimilarity(firstA+' '+lastA,firstB+' '+lastB);
    const aff=textSimilarity(a.Affiliation,b.Affiliation),country=textSimilarity(a.Country,b.Country);
    let score=.43*first+.43*last+.10*aff+.04*country;
    if(lastA===lastB&&first>=.72)score=Math.max(score,.86+.11*first);
    if(firstA===firstB&&last>=.72)score=Math.max(score,.86+.11*last);
    if(full>=.90)score=Math.max(score,full);
    return Math.min(1,score);
  }

  function mergeRoleValues(a,b) {
    return normalizeRoleList([a,b].filter(Boolean).join('; '));
  }

  function choosePeopleValue(current,incoming,strategy) {
    const a=String(current==null?'':current).trim(),b=String(incoming==null?'':incoming).trim();
    if(!a)return b;if(!b)return a;if(a===b)return a;
    if(strategy==='later')return b;
    if(strategy==='richer')return b.length>a.length?b:a;
    return a;
  }

  function mergePeopleRows(base,incoming,strategy,headers) {
    const out=Object.create(null);(headers||[]).forEach(h=>out[h]='');
    (headers||[]).forEach((h)=>{
      if(h==='Role')out[h]=mergeRoleValues(base&&base[h],incoming&&incoming[h]);
      else if(h==='Visible'){
        const a=base&&base[h],b=incoming&&incoming[h];out[h]=a?normalizeBooleanString(a):(b?normalizeBooleanString(b):'true');
      } else out[h]=choosePeopleValue(base&&base[h],incoming&&incoming[h],strategy);
    });
    if(!out.Category)out.Category=inferPeopleCategory(out.Role);
    return out;
  }

  function buildPeopleImportAnalysis(sources, includeCurrent, duplicateRule) {
    const entries=[];
    if(includeCurrent)state.people.rows.forEach((row,index)=>entries.push({row:Object.assign({},row),source:'Current People',sourceOrder:-1,rowIndex:index}));
    sources.forEach((source,sourceOrder)=>source.data.rows.forEach((row,rowIndex)=>entries.push({row:Object.assign({},row),source:source.file.name,sourceOrder,rowIndex})));
    const exactGroups=new Map();
    entries.forEach((entry,index)=>{entry.analysisIndex=index;const key=duplicateRule==='off'?'':peopleIdentityKey(entry.row,duplicateRule||'strict');if(!key)return;if(!exactGroups.has(key))exactGroups.set(key,[]);exactGroups.get(key).push(entry);});
    const exact=Array.from(exactGroups.values()).filter(group=>group.length>1);
    const exactPairs=new Set();exact.forEach(group=>{for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++)exactPairs.add(Math.min(group[i].analysisIndex,group[j].analysisIndex)+'|'+Math.max(group[i].analysisIndex,group[j].analysisIndex));});
    const similar=[];
    for(let i=0;i<entries.length;i++)for(let j=i+1;j<entries.length;j++){
      const pair=i+'|'+j;if(exactPairs.has(pair))continue;
      const score=similarPeopleScore(entries[i].row,entries[j].row);
      if(score>=.89)similar.push({a:entries[i],b:entries[j],score});
    }
    similar.sort((a,b)=>b.score-a.score);
    return {entries,exact,similar:similar.slice(0,120)};
  }

  function applyPeopleImportMerge(sources, options, similarSelections) {
    const currentRows=options.mode==='merge'?state.people.rows.map(row=>Object.assign({},row)):[];
    const sourceRows=[];sources.forEach((source)=>source.data.rows.forEach(row=>sourceRows.push(Object.assign({},row))));
    const headerSet=new Set(['First Name','Last Name','Category','Role','Affiliation','Country','Presentation Title','Presentation Type','Image','Visible']);
    if(options.mode==='merge')state.people.headers.forEach(h=>headerSet.add(h));sources.forEach(source=>source.data.headers.forEach(h=>headerSet.add(h)));
    const headers=Array.from(headerSet);
    let rows=currentRows.concat(sourceRows);
    const merged=[];
    const exactMap=new Map();
    rows.forEach((row)=>{
      const key=options.duplicateRule==='off'?'':peopleIdentityKey(row,options.duplicateRule||'strict');
      if(key&&exactMap.has(key)){
        const targetIndex=exactMap.get(key);merged[targetIndex]=mergePeopleRows(merged[targetIndex],row,options.conflict,headers);
      }else{
        const clean=mergePeopleRows({},row,options.conflict,headers);merged.push(clean);if(key)exactMap.set(key,merged.length-1);
      }
    });
    rows=merged;
    if(similarSelections&&similarSelections.length){
      similarSelections.forEach((pair)=>{
        const fpA=peopleRowFingerprint(pair.a.row),fpB=peopleRowFingerprint(pair.b.row);
        let ia=rows.findIndex(row=>peopleRowFingerprint(row)===fpA);
        let ib=rows.findIndex((row,index)=>index!==ia&&peopleRowFingerprint(row)===fpB);
        if(ia<0)ia=rows.reduce((best,row,index)=>{const score=similarPeopleScore(row,pair.a.row);return score>(best.score||0)?{index,score}:best;},{index:-1,score:0}).index;
        if(ib<0)ib=rows.reduce((best,row,index)=>{if(index===ia)return best;const score=similarPeopleScore(row,pair.b.row);return score>(best.score||0)?{index,score}:best;},{index:-1,score:0}).index;
        if(ia<0||ib<0||ia===ib)return;
        const keep=Math.min(ia,ib),drop=Math.max(ia,ib);rows[keep]=mergePeopleRows(rows[keep],rows[drop],options.conflict,headers);rows.splice(drop,1);
      });
    }
    state.people.headers=headers;
    state.people.rows=rows.filter(row=>row['First Name']||row['Last Name']);
  }

  async function openPeopleImportAssistant(sources, onImported) {
    const initialMode=state.people.rows.length?'merge':'replace';
    const analysisCache=new Map();
    els.modalBackdrop.classList.remove('preview-modal');els.modalBackdrop.classList.add('people-import-modal');
    els.modalTitle.textContent='People import & merge';els.modalBody.replaceChildren();
    const root=div('people-import-assistant');
    const intro=div('people-import-intro');intro.innerHTML='<div class="eyebrow">Multi-file merge</div><p>Review how Excel/CSV sources are combined before changing People. Roles are always unioned; Response and administrative columns never filter rows.</p><p class="people-import-role-note"><b>Committee roles:</b> Chairman → Organizing Committee; Program Committee + Chairman → Program Committee Chairman; Organizer / Local Organizer → Local Organizers; plain Committee → Program Committee. Specific roles are preserved instead of being flattened.</p>';root.append(intro);
    const sourceGrid=div('people-import-sources');sources.forEach((source)=>{const card=div('people-import-source');const images=Array.isArray(source.bundleAssets)?source.bundleAssets.length:0;card.append(div('people-import-source-name',source.file.name),div('people-import-source-meta',source.data.rows.length+' people · '+source.data.headers.length+' mapped columns'+(images?' · '+images+' face images':'')));sourceGrid.append(card);});root.append(sourceGrid);
    const controls=div('people-import-controls');
    const modeLabel=document.createElement('label');modeLabel.textContent='Import mode';const mode=document.createElement('select');[['merge','Merge with current People'],['replace','Replace current People with imported files']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;mode.append(o);});mode.value=initialMode;modeLabel.append(mode);
    const conflictLabel=document.createElement('label');conflictLabel.textContent='Conflicting values';const conflict=document.createElement('select');[['earlier','Keep existing / earlier source'],['later','Prefer later imported file'],['richer','Prefer richer (longer) value']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;conflict.append(o);});conflict.value='richer';conflictLabel.append(conflict);
    const duplicateLabel=document.createElement('label');duplicateLabel.textContent='Automatic duplicate rule';const duplicateRule=document.createElement('select');[['strict','Safe · same name + affiliation/country'],['name','Aggressive · same normalized first + last name'],['off','Off · review duplicates manually']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;duplicateRule.append(o);});duplicateRule.value='strict';duplicateLabel.append(duplicateRule);
    const duplicateNote=div('people-import-toggle');duplicateNote.innerHTML='<div><b>Roles are always unioned</b><span>Automatic merges fill blank fields and preserve all roles. Similar-name pairs stay manual suggestions below.</span></div>';
    controls.append(modeLabel,conflictLabel,duplicateLabel,duplicateNote);root.append(controls);
    const stats=div('people-import-stats');root.append(stats);
    const similarSection=div('people-import-similar');const similarHead=div('people-import-similar-head');similarHead.innerHTML='<div><b>Similar people</b><span>Possible duplicates are suggestions only. Tick the pairs you really want to merge.</span></div>';const similarCount=div('sheet-meta');similarHead.append(similarCount);const similarList=div('people-import-similar-list');similarSection.append(similarHead,similarList);root.append(similarSection);
    const footer=div('people-import-footer');const cancel=button('Cancel','button ghost'),apply=button('Apply import','button primary');footer.append(cancel,apply);root.append(footer);
    const selectedPairs=new Set();
    function activeAnalysis(){const key=mode.value+'|'+duplicateRule.value;if(!analysisCache.has(key))analysisCache.set(key,buildPeopleImportAnalysis(sources,mode.value==='merge',duplicateRule.value));return analysisCache.get(key);}
    function renderAnalysis(){const a=activeAnalysis();stats.replaceChildren();const incoming=sources.reduce((n,s)=>n+s.data.rows.length,0);const current=mode.value==='merge'?state.people.rows.length:0;const exactExtra=a.exact.reduce((n,g)=>n+g.length-1,0);stats.append(div('people-import-stat',incoming+' imported rows'),div('people-import-stat',current+' current rows'),div('people-import-stat',exactExtra+' exact duplicate'+(exactExtra===1?'':'s')),div('people-import-stat',a.similar.length+' similar suggestion'+(a.similar.length===1?'':'s')));
      similarList.replaceChildren();selectedPairs.clear();similarCount.textContent=a.similar.length+' suggestions';
      if(!a.similar.length){similarList.append(div('sheet-empty','No similar-name duplicates found.'));return;}
      a.similar.forEach((pair,index)=>{const lab=document.createElement('label');lab.className='people-import-similar-row';const cb=document.createElement('input');cb.type='checkbox';cb.dataset.index=String(index);const body=div('people-import-similar-copy');const names=div('people-import-similar-names',peopleDisplayName(pair.a.row)+'  ↔  '+peopleDisplayName(pair.b.row));const detail=div('people-import-similar-detail',[pair.a.row.Affiliation,pair.a.source].filter(Boolean).join(' · ')+'  /  '+[pair.b.row.Affiliation,pair.b.source].filter(Boolean).join(' · '));const score=div('people-import-score',Math.round(pair.score*100)+'%');body.append(names,detail);lab.append(cb,body,score);cb.addEventListener('change',()=>{cb.checked?selectedPairs.add(index):selectedPairs.delete(index);});similarList.append(lab);});
    }
    mode.addEventListener('change',renderAnalysis);duplicateRule.addEventListener('change',renderAnalysis);renderAnalysis();
    cancel.addEventListener('click',()=>closeModal());
    apply.addEventListener('click',async()=>{const a=activeAnalysis(),pairs=Array.from(selectedPairs).map(i=>a.similar[i]).filter(Boolean);const options={mode:mode.value,conflict:conflict.value,duplicateRule:duplicateRule.value};const before=state.people.rows.length;applyPeopleImportMerge(sources,options,pairs);const bundleAssets=sources.flatMap((source)=>Array.isArray(source.bundleAssets)?source.bundleAssets:[]);for(const asset of bundleAssets){if(asset&&asset.path&&asset.blob)await writeProjectBlob(asset.path,asset.blob);}markSheetDirty('people');resetDocumentSelections();closeModal();if(bundleAssets.length)await loadAssets();if(typeof onImported==='function')onImported();else renderPeopleSpreadsheet();const imported=sources.reduce((n,s)=>n+s.data.rows.length,0);toast('People import ready: '+state.people.rows.length+' people after merge'+(bundleAssets.length?' · '+bundleAssets.length+' face images imported':'')+'. Save people.csv to persist it.','success');log('info','people.multi_import_applied',{files:sources.map(s=>s.file.name),imported,before,after:state.people.rows.length,mode:options.mode,duplicateRule:options.duplicateRule,similarMerged:pairs.length,conflict:options.conflict,bundleImages:bundleAssets.length});});
    els.modalBody.append(root);els.modalBackdrop.classList.remove('hidden');
  }

  async function importPeopleSpreadsheetFiles(files, onImported) {
    try {
      const sources=[];
      for(const file of files){const {ext,parsed}=await parseSpreadsheetFile(file);const data=normalizePeopleData(preparePeopleImport(parsed));sources.push({file,ext,parsed,data});}
      await openPeopleImportAssistant(sources,onImported);
      log('info','people.multi_import_analyzed',{files:sources.map(s=>s.file.name),rows:sources.reduce((n,s)=>n+s.data.rows.length,0)});
    } catch(error) {
      log('error','spreadsheet.import_failed',{kind:'people',files:files.map(f=>f.name),message:error.message});toast('People import failed: '+error.message,'error');
    }
  }

  async function importSheetFile(kind, file, onImported) {
    if(kind==='people'){await importPeopleSpreadsheetFiles([file],onImported);return;}
    try {
      const {ext,parsed}=await parseSpreadsheetFile(file);
      const candidateRows=parsed.rows.length;
      if (!await totemConfirm('Replace '+humanizeKey(kind)+' data','Replace the current '+kind+' table with '+candidateRows+' source rows from '+file.name+'?','Replace table',{danger:true})) return;
      if (kind === 'program') normalizeProgramSheet(parsed);
      else { state[kind].headers = parsed.headers; state[kind].rows = parsed.rows; }
      markSheetDirty(kind);
      if (typeof onImported === 'function') onImported(); else renderSpreadsheet(kind);
      log('info','spreadsheet.imported',{kind,format:ext,rows:state[kind].rows.length,sourceRows:parsed.rows.length,file:file.name,sheet:parsed.sheet||null});
      toast('Imported '+state[kind].rows.length+' rows from '+file.name+'.','success');
    } catch (error) {
      log('error','spreadsheet.import_failed',{kind,file:file.name,message:error.message});toast('Spreadsheet import failed: '+error.message,'error');
    }
  }

  function renderGenericSpreadsheet(kind) {
    const container = kind === 'people' ? els.peopleEditor : els.programEditor;
    const sheet = state[kind];
    container.replaceChildren();
    if (!state.config) { renderNoProjectSheets(); return; }
    if (kind === 'program') renderProgramPdfControls(container);
    const shell = div('spreadsheet-shell ' + kind + '-sheet');
    const toolbar = div('sheet-toolbar');
    const addBtn = button(kind === 'program' ? 'Add item' : 'Add row', 'button'); toolbar.append(addBtn);
    if (kind === 'program') { const manageTypes = button('Manage types','button ghost'); manageTypes.addEventListener('click', openProgramTypeManager); toolbar.append(manageTypes); }
    sheetImportControls(kind, toolbar, () => renderGenericSpreadsheet(kind));
    const searchLabel = document.createElement('label'); searchLabel.textContent = 'Filter';
    const search = document.createElement('input'); search.type='search'; search.placeholder='Search…'; searchLabel.append(search); toolbar.append(searchLabel);
    const meta = div('sheet-meta', sheet.rows.length + ' rows · ' + sheet.headers.length + ' columns'); toolbar.append(meta);
    const scroll = div('sheet-scroll'); shell.append(toolbar, scroll); container.append(shell); bindResponsiveSpreadsheet(shell,kind);

    const renderTable = () => {
      const query = search.value.trim().toLowerCase(); scroll.replaceChildren();
      const table = document.createElement('table'); table.className='sheet-table'; applySpreadsheetColumns(table,kind,sheet.headers);
      const thead=document.createElement('thead'), hr=document.createElement('tr');
      const rh=document.createElement('th'); rh.className='row-number'; rh.textContent='#'; hr.append(rh);
      sheet.headers.forEach((header)=>{const th=document.createElement('th');th.textContent=header;hr.append(th);});
      const ah=document.createElement('th');ah.textContent='Row';hr.append(ah);thead.append(hr);table.append(thead);
      const tbody=document.createElement('tbody');
      sheet.rows.forEach((row,rowIndex)=>{
        if(query&&!sheet.headers.some((h)=>String(row[h]||'').toLowerCase().includes(query)))return;
        const tr=document.createElement('tr'),rn=document.createElement('td');rn.className='row-number';rn.textContent=String(rowIndex+1);tr.append(rn);
        sheet.headers.forEach((header,colIndex)=>{
          const td=document.createElement('td'); if(/title|notes|description|affiliation/i.test(header))td.classList.add('wide');if(/notes|description/i.test(header))td.classList.add('very-wide');
          let input;
          if(/^Visible$/i.test(header)){
            input=document.createElement('select');['true','false'].forEach((v)=>{const o=document.createElement('option');o.value=v;o.textContent=v;input.append(o);});input.value=normalizeBooleanString(row[header]);
          } else if(kind === 'program' && /^Type$/i.test(header)) {
            input = document.createElement('select');
            const types = programTypes(); const values = types.concat(row[header] && !types.includes(row[header]) ? [row[header]] : []);
            values.forEach((v)=>{const o=document.createElement('option');o.value=v;o.textContent=v;input.append(o);}); input.value=row[header]||values[0]||'';
          } else if(kind === 'program' && /^Date$/i.test(header)) {
            input = buildProgramDateSelect(row, rowIndex);
          } else {
            input=document.createElement('input');input.type='text';input.value=row[header]==null?'':String(row[header]);
          }
          input.dataset.row=String(rowIndex);input.dataset.col=String(colIndex);input.dataset.kind=kind;
          if (!(kind === 'program' && /^Date$/i.test(header))) {
            input.addEventListener('input',()=>{row[header]=input.value;markSheetDirty(kind);});
            input.addEventListener('change',()=>{row[header]=input.value;markSheetDirty(kind);});
            input.addEventListener('paste',handleSpreadsheetPaste);
          }
          td.append(input);tr.append(td);
        });
        const action=document.createElement('td');action.className='action-cell';const copy=button('Copy','button ghost'),del=button('Delete','button ghost');
        copy.addEventListener('click',()=>{const clone=Object.create(null);sheet.headers.forEach((h)=>clone[h]=row[h]||'');sheet.rows.splice(rowIndex+1,0,clone);markSheetDirty(kind);renderGenericSpreadsheet(kind);});
        del.addEventListener('click',async()=>{if(!await totemConfirm('Delete row','Delete row '+(rowIndex+1)+'?','Delete',{danger:true}))return;sheet.rows.splice(rowIndex,1);markSheetDirty(kind);renderGenericSpreadsheet(kind);});action.append(copy,del);tr.append(action);
        Array.from(tr.children).forEach((cell,index)=>{cell.dataset.label=index===0?'#':(index<=sheet.headers.length?(sheet.headers[index-1]||''):'Row');});
        tbody.append(tr);
      });
      table.append(tbody);scroll.append(table);if(!tbody.children.length)scroll.append(div('sheet-empty',query?'No rows match the filter.':'No rows.'));
    };
    addBtn.addEventListener('click',()=>{const row=Object.create(null);sheet.headers.forEach((h)=>row[h]=/^Visible$/i.test(h)?'true':'');sheet.rows.push(row);markSheetDirty(kind);renderGenericSpreadsheet(kind);});
    search.addEventListener('input',renderTable);renderTable();
  }

  function ensureProgramDownloadConfig() {
    if(!state.config.program||typeof state.config.program!=='object')state.config.program={};
    if(!state.config.program.download||typeof state.config.program.download!=='object')state.config.program.download={};
    const cfg=state.config.program.download;
    if(cfg.enabled==null)cfg.enabled=true;
    if(!cfg.mode)cfg.mode='generated';
    if(!cfg.label)cfg.label='Download program PDF';
    if(!cfg.local_file)cfg.local_file='assets/documents/program.pdf';
    if(!cfg.local_filename)cfg.local_filename=(state.projectName||'conference')+'-program.pdf';
    if(!cfg.generated_filename)cfg.generated_filename=(state.projectName||'conference')+'-program.pdf';
    return cfg;
  }

  function programPdfAssets() {
    return state.assets.filter((asset)=>asset.path.startsWith('assets/documents/')&&extensionOf(asset.path)==='pdf');
  }

  async function chooseAndUploadProgramPdf(afterUpload) {
    const input=document.createElement('input');input.type='file';input.accept='application/pdf,.pdf';input.hidden=true;document.body.append(input);
    input.addEventListener('change',async()=>{
      const file=input.files&&input.files[0];input.remove();if(!file)return;
      try{
        const clean=file.name.replace(/[^A-Za-z0-9._-]+/g,'-')||'program.pdf';
        const path='assets/documents/'+clean;
        await writeProjectBlob(path,file);
        const cfg=ensureProgramDownloadConfig();cfg.enabled=true;cfg.mode='local';cfg.local_file=path;cfg.local_filename=clean;
        syncStructuredYaml('program.pdf_uploaded',{path,bytes:file.size});await loadAssets();renderGenericSpreadsheet('program');
        if (typeof afterUpload === 'function') afterUpload(path);
        toast('Program PDF uploaded and selected: '+path,'success');
      }catch(error){log('error','program.pdf_upload_failed',{message:error.message});toast('Could not upload Program PDF: '+error.message,'error');}
    });
    input.click();
  }

  function renderProgramPdfControls(container) {
    const cfg=ensureProgramDownloadConfig();
    const panel=div('panel program-pdf-panel');
    const head=div('panel-head');const copy=div('');copy.innerHTML='<div class="eyebrow">Public program download</div><h2>Program PDF</h2><p>Choose whether visitors download a local PDF you supply or a PDF generated from the current program.csv.</p>';head.append(copy);panel.append(head);
    const fields=div('program-pdf-fields');
    const sourceLabel=document.createElement('label');sourceLabel.textContent='Download source';const source=document.createElement('select');[['generated','Generate from program.csv'],['local','Use uploaded local PDF'],['disabled','Hide PDF download']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;source.append(o);});source.value=cfg.enabled===false?'disabled':(String(cfg.mode||'generated').toLowerCase()==='local'?'local':'generated');sourceLabel.append(source);
    const labelField=document.createElement('label');labelField.textContent='Button label';const labelInput=document.createElement('input');labelInput.value=cfg.label||'Download program PDF';labelField.append(labelInput);
    const fileField=document.createElement('label');fileField.textContent='Local PDF';const fileSelect=document.createElement('select');const current=String(cfg.local_file||'');const pdfs=programPdfAssets();const empty=document.createElement('option');empty.value='';empty.textContent='Choose a PDF…';fileSelect.append(empty);pdfs.forEach((asset)=>{const o=document.createElement('option');o.value=asset.path;o.textContent=asset.path;fileSelect.append(o);});if(current&&!pdfs.some((asset)=>asset.path===current)){const o=document.createElement('option');o.value=current;o.textContent=current+' (configured)';fileSelect.append(o);}fileSelect.value=current;fileField.append(fileSelect);
    const filenameField=document.createElement('label');filenameField.textContent='Generated filename';const filenameInput=document.createElement('input');filenameInput.value=cfg.generated_filename||((state.projectName||'conference')+'-program.pdf');filenameField.append(filenameInput);
    fields.append(sourceLabel,labelField,fileField,filenameField);panel.append(fields);
    const actions=div('inline-actions');const upload=button('Upload program PDF','button');const openAssets=button('Open Assets','button ghost');const status=div('program-pdf-status','');actions.append(upload,openAssets,status);panel.append(actions);
    function updateStatus(){const mode=source.value;if(mode==='local')status.textContent=cfg.local_file?'Website downloads '+cfg.local_file:'Choose or upload a PDF before publishing.';else if(mode==='generated')status.textContent='Website generates the PDF from '+state.program.path+'.';else status.textContent='The Program PDF download button is hidden.';fileField.classList.toggle('field-muted',mode!=='local');filenameField.classList.toggle('field-muted',mode!=='generated');}
    source.addEventListener('change',()=>{cfg.enabled=source.value!=='disabled';if(source.value!=='disabled')cfg.mode=source.value;syncStructuredYaml('program.download_mode_changed',{mode:source.value});updateStatus();});
    labelInput.addEventListener('change',()=>{cfg.label=labelInput.value.trim()||'Download program PDF';syncStructuredYaml('program.download_label_changed',{});});
    fileSelect.addEventListener('change',()=>{cfg.local_file=fileSelect.value;if(fileSelect.value)cfg.local_filename=fileNameFromPath(fileSelect.value);syncStructuredYaml('program.download_local_changed',{path:fileSelect.value});updateStatus();});
    filenameInput.addEventListener('change',()=>{cfg.generated_filename=filenameInput.value.trim()||((state.projectName||'conference')+'-program.pdf');syncStructuredYaml('program.download_filename_changed',{});});
    upload.addEventListener('click',chooseAndUploadProgramPdf);openAssets.addEventListener('click',()=>switchView('assets'));updateStatus();container.append(panel);
  }

  function programTypes() {
    const configured = getConfig('program.item_types', getConfig('program.types', []));
    const values = (Array.isArray(configured) ? configured : []).map((item)=>typeof item==='object'?(item.label||item.name||item.id):item).filter(Boolean).map(String);
    state.program.rows.forEach((row)=>{ if(row.Type && !values.includes(row.Type)) values.push(row.Type); });
    return values.length ? values : ['Session','Talk','Keynote','Lecture','Break','Lunch','Registration','Social event','Other'];
  }

  function openProgramTypeManager() {
    const rows = programTypes().map((value)=>({ original:value, value }));
    els.modalTitle.textContent='Manage program item types'; els.modalBody.replaceChildren(); const list=div('role-manager-list');
    const render=()=>{list.replaceChildren();rows.forEach((item,index)=>{const row=div('role-manager-row');const input=document.createElement('input');input.value=item.value;input.addEventListener('input',()=>item.value=input.value);const del=button('Delete','button ghost');del.addEventListener('click',()=>{rows.splice(index,1);render();});row.append(input,del);list.append(row);});}; render();
    const actions=div('inline-actions'),add=button('Add type','button'),save=button('Save types','button primary');
    add.addEventListener('click',()=>{rows.push({original:'',value:'New type'});render();});
    save.addEventListener('click',()=>{const cleaned=Array.from(new Set(rows.map((r)=>String(r.value||'').trim()).filter(Boolean)));const rename=new Map(rows.filter((r)=>r.original&&r.value&&r.original!==r.value).map((r)=>[r.original,r.value]));state.program.rows.forEach((item)=>{if(rename.has(item.Type))item.Type=rename.get(item.Type);if(item.Type&&!cleaned.includes(item.Type))item.Type='';});if(!state.config.program||typeof state.config.program!=='object')state.config.program={};state.config.program.item_types=cleaned;syncStructuredYaml('program.types_updated',{count:cleaned.length});markSheetDirty('program');closeModal();renderGenericSpreadsheet('program');});
    actions.append(add,save);els.modalBody.append(list,actions);els.modalBackdrop.classList.remove('hidden');
  }

  function conferenceProgramDates() {
    const start = String(getConfig('conference.start_date', '') || '').trim();
    const end = String(getConfig('conference.end_date', '') || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return [];
    const startDate = new Date(start + 'T00:00:00Z');
    const endDate = new Date(end + 'T00:00:00Z');
    if (!Number.isFinite(startDate.getTime()) || !Number.isFinite(endDate.getTime()) || endDate < startDate) return [];
    const result = [];
    for (let cursor = new Date(startDate), index = 1; cursor <= endDate && result.length < 60; cursor.setUTCDate(cursor.getUTCDate() + 1), index += 1) {
      const iso = cursor.toISOString().slice(0, 10);
      result.push({ iso, label: formatProgramDate(iso), day: 'Day ' + index });
    }
    return result;
  }

  function formatProgramDate(iso) {
    const date = new Date(iso + 'T00:00:00Z');
    return new Intl.DateTimeFormat('en-US', { weekday:'long', month:'long', day:'numeric', year:'numeric', timeZone:'UTC' }).format(date);
  }

  function normalizeProgramDateToIso(value) {
    const text = String(value || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
    if (!text || /^TBC$/i.test(text)) return '';
    const parsed = new Date(text);
    return Number.isFinite(parsed.getTime()) ? parsed.toISOString().slice(0,10) : '';
  }

  function buildProgramDateSelect(row, rowIndex) {
    const select = document.createElement('select');
    select.className = 'program-date-select';
    const current = String(row.Date || '');
    const currentIso = normalizeProgramDateToIso(current);
    const dates = conferenceProgramDates();
    const tbc = document.createElement('option'); tbc.value = ''; tbc.textContent = dates.length ? 'TBC / choose date' : 'Set conference dates in YAML first'; select.append(tbc);
    dates.forEach((item) => {
      const option = document.createElement('option'); option.value = item.iso; option.textContent = item.label; select.append(option);
    });
    if (current && currentIso && !dates.some((item) => item.iso === currentIso)) {
      const option = document.createElement('option'); option.value = currentIso; option.textContent = current + ' (outside range)'; select.append(option);
    } else if (current && !currentIso && !/^TBC$/i.test(current)) {
      const option = document.createElement('option'); option.value = '__legacy__'; option.textContent = current + ' (legacy value)'; select.append(option); select.value = '__legacy__';
    }
    if (currentIso) select.value = currentIso;
    else if (!current || /^TBC$/i.test(current)) select.value = '';
    select.addEventListener('change', () => {
      if (select.value === '__legacy__') return;
      const match = dates.find((item) => item.iso === select.value);
      row.Date = match ? match.label : 'TBC';
      const dayHeader = state.program.headers.find((header) => /^Day$/i.test(header));
      if (dayHeader && match) row[dayHeader] = match.day;
      markSheetDirty('program');
      renderGenericSpreadsheet('program');
      log('debug', 'program.date_selected', { row: rowIndex + 1, date: match ? match.iso : 'TBC' });
    });
    return select;
  }

  function peopleCategories() {
    const configured = getConfig('people.categories', []);
    const values = (Array.isArray(configured) ? configured : []).map((item) => typeof item === 'object' ? (item.label || item.id || item.value) : item).filter(Boolean).map(String);
    const defaults = ['Speaker','Lecturer','Committee','Organizer','Presenter','Other'];
    state.people.rows.forEach((row)=>{ if(row.Category && !values.includes(row.Category)) values.push(row.Category); });
    return values.length ? values : defaults;
  }

  function peopleRoles() {
    const values=[];
    const peopleConfig=state.config&&state.config.people&&typeof state.config.people==='object'?state.config.people:null;
    const hasConfiguredRoles=Boolean(peopleConfig&&Object.prototype.hasOwnProperty.call(peopleConfig,'roles'));
    const configured=getConfig('people.roles',[]); (Array.isArray(configured)?configured:[]).forEach((role)=>{const raw=typeof role==='object'?(role.label||role.name||role.id):role;const value=canonicalPeopleRoleToken(raw);if(value&&!values.includes(String(value)))values.push(String(value));});
    if(!hasConfiguredRoles){const groups=getConfig('people.groups',[]); (Array.isArray(groups)?groups:[]).forEach((g)=>{const value=g&&g.role?canonicalPeopleRoleToken(g.role):'';if(value&&!values.includes(String(value)))values.push(String(value));});}
    state.people.rows.forEach((row)=>{String(row.Role||'').split(/[;,|]/).map((x)=>x.trim()).filter(Boolean).forEach((role)=>{if(!values.includes(role))values.push(role);});});
    if(values.length||hasConfiguredRoles)return values;
    return ['Invited Speaker','Speaker','Lecturer','Organizing Committee','Chairman','Co-chairman','Program Committee','Program Committee Chairman','Program Committee Co-chairman','Local Organizer','Presenter'];
  }

  function peopleImageAssets() { return state.assets.filter((asset)=>asset.path.startsWith('assets/people/')&&PREVIEWABLE_EXTENSIONS.has(extensionOf(asset.path))); }

  function assetPreviewUrl(path) {
    const asset=state.assets.find((item)=>item.path===path); if(!asset)return '';
    if (asset._previewUrl) return asset._previewUrl;
    const url=URL.createObjectURL(asset.blob);asset._previewUrl=url;state.objectUrls.push(url);return url;
  }

  function renderPeopleRolesSection(container) {
    const panel=div('people-roles-panel panel');
    const head=div('people-roles-head');
    const copy=div('people-roles-copy');
    copy.innerHTML='<div class="eyebrow">conference.yaml · people.roles</div><h2>Roles</h2><p>Define the roles available for people. Renaming or deleting a role updates existing assignments when you save.</p>';
    const rows=peopleRoles().map((role)=>({original:role,value:role}));
    const list=div('people-roles-list');
    const actions=div('people-roles-actions');
    const addRole=button('Add role','button ghost');
    const saveRoles=button('Save roles','button primary');
    actions.append(addRole,saveRoles); head.append(copy,actions); panel.append(head,list); container.append(panel);

    const usage=(role)=>state.people.rows.reduce((count,person)=>count+(String(person.Role||'').split(/[;,|]/).map((x)=>x.trim()).filter(Boolean).includes(role)?1:0),0);
    const render=()=>{
      list.replaceChildren();
      if(!rows.length) list.append(div('people-roles-empty','No roles defined yet. Add the first role below.'));
      rows.forEach((item,index)=>{
        const row=div('people-role-row');
        const field=document.createElement('label'); field.className='people-role-field';
        const label=document.createElement('span'); label.textContent='Role name';
        const input=document.createElement('input'); input.value=item.value; input.placeholder='e.g. Invited Speaker';
        input.addEventListener('input',()=>{item.value=input.value;});
        field.append(label,input);
        const count=div('people-role-count',String(usage(item.original||item.value))+' assigned');
        const del=button('Delete','button ghost'); del.addEventListener('click',()=>{rows.splice(index,1);render();});
        row.append(field,count,del); list.append(row);
      });
    };
    addRole.addEventListener('click',()=>{rows.push({original:'',value:''});render();const inputs=list.querySelectorAll('input');inputs[inputs.length-1]?.focus();});
    saveRoles.addEventListener('click',()=>{applyPeopleRoles(rows);renderPeopleSpreadsheet();});
    render();
  }

  function applyPeopleRoles(rows) {
    const cleaned=Array.from(new Set(rows.map((r)=>canonicalPeopleRoleToken(r.value)).filter(Boolean)));
    const rename=new Map(rows.filter((r)=>r.original&&r.value&&r.original!==String(r.value).trim()).map((r)=>[r.original,String(r.value).trim()]));
    state.people.rows.forEach((person)=>{
      const next=String(person.Role||'').split(/[;,|]/).map((x)=>x.trim()).filter(Boolean).map((role)=>rename.get(role)||role).filter((role)=>cleaned.includes(role));
      person.Role=Array.from(new Set(next)).join('; ');
    });
    if(!state.config.people||typeof state.config.people!=='object') state.config.people={};
    const groups=Array.isArray(state.config.people.groups)?state.config.people.groups:[];
    groups.forEach((group)=>{if(!group)return;const original=String(group.role||'');group.role=canonicalPeopleRoleToken(rename.get(original)||original)||original;});
    state.config.people.roles=cleaned;
    syncStructuredYaml('people.roles_updated',{count:cleaned.length});
    markSheetDirty('people');
    toast('People roles updated','success');
  }

  function dummyPeopleRows() {
    const rows=[
      ['First Name 01','Last Name 01','Speaker','Invited Speaker','Affiliation 01','Country 01','Presentation Title 01','Invited lecture'],
      ['First Name 02','Last Name 02','Speaker','Invited Speaker','Affiliation 02','Country 02','Presentation Title 02','Invited lecture'],
      ['First Name 03','Last Name 03','Speaker','Invited Speaker','Affiliation 03','Country 03','Presentation Title 03','Invited lecture'],
      ['First Name 04','Last Name 04','Speaker','Invited Speaker','Affiliation 04','Country 04','Presentation Title 04','Invited lecture'],
      ['First Name 05','Last Name 05','Lecturer','School Lecturer','Affiliation 05','Country 05','Presentation Title 05','School lecture'],
      ['First Name 06','Last Name 06','Lecturer','School Lecturer','Affiliation 06','Country 06','Presentation Title 06','School lecture'],
      ['First Name 07','Last Name 07','Committee','Organizing Committee','Affiliation 07','Country 07','',''],
      ['First Name 08','Last Name 08','Organizer','Organizing Committee; Local Organizer','Affiliation 08','Country 08','',''],
      ['First Name 09','Last Name 09','Committee','Program Committee','Affiliation 09','Country 09','',''],
      ['First Name 10','Last Name 10','Committee','Program Committee','Affiliation 10','Country 10','',''],
      ['First Name 11','Last Name 11','Presenter','Oral Speaker','Affiliation 11','Country 11','Presentation Title 11','Oral contribution'],
      ['First Name 12','Last Name 12','Presenter','Poster Presenter','Affiliation 12','Country 12','Presentation Title 12','Poster']
    ];
    const headers=state.people.headers.length?state.people.headers.slice():['First Name','Last Name','Category','Role','Affiliation','Country','Presentation Title','Presentation Type','Image','Visible'];
    return rows.map((values)=>{
      const row=Object.create(null);headers.forEach((h)=>row[h]='');
      ['First Name','Last Name','Category','Role','Affiliation','Country','Presentation Title','Presentation Type'].forEach((h,i)=>{if(headers.includes(h))row[h]=values[i]||'';});
      if(headers.includes('Image'))row.Image='';
      if(headers.includes('Visible'))row.Visible='true';
      return row;
    });
  }

  async function togglePeopleDemo() {
    if (!state.config) return;
    if (state.peopleDemo.active) {
      const snap=state.peopleDemo.snapshot;
      if(snap){state.people.headers=snap.headers.slice();state.people.rows=snap.rows.map((row)=>Object.assign(Object.create(null),row));state.people.dirty=Boolean(snap.dirty);}
      state.peopleDemo.active=false;state.peopleDemo.snapshot=null;
      resetDocumentSelections();renderPeopleSpreadsheet();updateSaveState();
      log('info','people.demo_restored',{rows:state.people.rows.length,dirty:state.people.dirty});
      toast('Previous People restored. Demo rows discarded because they were not saved.','success');
      return;
    }
    if (state.people.dirty) {
      const proceed=await totemConfirm('Load demo People','Your current unsaved People rows will be kept in memory so you can restore them until you save the demo list. Continue?','Load demo People');
      if(!proceed)return;
    }
    state.peopleDemo.snapshot={headers:state.people.headers.slice(),rows:state.people.rows.map((row)=>Object.assign(Object.create(null),row)),dirty:state.people.dirty};
    state.people.rows=dummyPeopleRows();state.people.dirty=true;state.peopleDemo.active=true;
    resetDocumentSelections();renderPeopleSpreadsheet();updateSaveState();els.savePeopleBtn.disabled=false;
    log('info','people.demo_loaded',{rows:state.people.rows.length});
    toast('Demo People loaded. Edit them freely; Save People or Save all writes them to people.csv.','success');
  }

  function renderPeopleSpreadsheet() {
    const container=els.peopleEditor,sheet=state.people;container.replaceChildren();if(!state.config){renderNoProjectSheets();return;}
    renderPeopleRolesSection(container);
    const shell=div('spreadsheet-shell people-sheet'),toolbar=div('sheet-toolbar');const add=button('Add person','button'),demoPeople=button(state.peopleDemo.active?'Restore previous People':'Use demo People','button ghost'),clearPeople=button('Clear People','button ghost danger');toolbar.append(add,demoPeople,clearPeople);sheetImportControls('people',toolbar,renderPeopleSpreadsheet);
    const searchLabel=document.createElement('label');searchLabel.textContent='Filter';const search=document.createElement('input');search.type='search';search.placeholder='Name, role, title, affiliation…';searchLabel.append(search);toolbar.append(searchLabel);
    const catLabel=document.createElement('label');catLabel.textContent='Category';const catFilter=document.createElement('select');catFilter.className='category-filter';const all=document.createElement('option');all.value='';all.textContent='All';catFilter.append(all);peopleCategories().forEach((v)=>{const o=document.createElement('option');o.value=v;o.textContent=v;catFilter.append(o);});catLabel.append(catFilter);toolbar.append(catLabel);
    toolbar.append(div('sheet-meta',sheet.rows.length+' people'));
    if(state.peopleDemo.active){const demoNote=div('people-demo-banner');demoNote.innerHTML='<b>Demo People loaded</b><span>These are normal editable rows. Save People or Save all to write them to people.csv. Restore previous People is available only until you save.</span>';shell.append(demoNote);}
    const scroll=div('sheet-scroll');shell.append(toolbar,scroll);container.append(shell);bindResponsiveSpreadsheet(shell,'people');
    demoPeople.addEventListener('click',togglePeopleDemo);
    const fixedCore=['First Name','Last Name','Category','Role','Affiliation','Country'];
    const preferredContribution=['Presentation Title','Presentation Type'].filter((header)=>sheet.headers.includes(header));
    const reserved=new Set(fixedCore.concat(preferredContribution,['Image','Visible']));
    const extraHeaders=sheet.headers.filter((header)=>!reserved.has(header));
    const editableHeaders=fixedCore.concat(preferredContribution,extraHeaders);
    const visibleHeaders=['Photo'].concat(editableHeaders,['Image','Visible','Row']);
    const renderTextCell=(tr,row,rowIndex,header)=>{
      const td=document.createElement('td');if(/affiliation|title|abstract|talk|contribution|notes|description/i.test(header))td.classList.add('wide');if(/title|abstract|talk|contribution/i.test(header))td.classList.add('presentation-title-cell');
      const input=document.createElement('input');input.type='text';input.value=row[header]||'';input.title=row[header]||'';input.dataset.kind='people';input.dataset.row=String(rowIndex);input.dataset.col=String(sheet.headers.indexOf(header));
      input.addEventListener('input',()=>{row[header]=input.value;input.title=input.value;markSheetDirty('people');});input.addEventListener('paste',handleSpreadsheetPaste);td.append(input);tr.append(td);
    };
    const renderTable=()=>{
      const query=search.value.trim().toLowerCase(),category=catFilter.value;scroll.replaceChildren();const table=document.createElement('table');table.className='sheet-table';
      applySpreadsheetColumns(table,'people',visibleHeaders.slice(0,-1));const thead=document.createElement('thead'),trh=document.createElement('tr');const rh=document.createElement('th');rh.className='row-number';rh.textContent='#';trh.append(rh);visibleHeaders.forEach((h)=>{const th=document.createElement('th');th.textContent=h;trh.append(th);});thead.append(trh);table.append(thead);const tbody=document.createElement('tbody');
      const categories=peopleCategories(),images=peopleImageAssets();
      sheet.rows.forEach((row,rowIndex)=>{
        const hay=sheet.headers.map((header)=>String(row[header]||'')).join(' ').toLowerCase();if(query&&!hay.includes(query))return;if(category&&row.Category!==category)return;
        const tr=document.createElement('tr'),rn=document.createElement('td');rn.className='row-number';rn.textContent=String(rowIndex+1);tr.append(rn);
        const photoTd=document.createElement('td');photoTd.className='person-photo-cell';
        const photoButton=button('', 'person-thumb-button');photoButton.title='Change face image';photoButton.setAttribute('aria-label','Change face image');
        const img=document.createElement('img');img.className='person-thumb';img.alt='';img.src=assetPreviewUrl(row.Image)||assetPreviewUrl(getConfig('assets.people_fallback','assets/people/no_face.jpg'));
        const editBadge=div('person-thumb-edit','Edit');photoButton.append(img,editBadge);photoButton.addEventListener('click',()=>openPersonImagePicker(rowIndex));photoTd.append(photoButton);tr.append(photoTd);
        fixedCore.forEach((header)=>{
          if(header==='Category'){
            const td=document.createElement('td'),select=document.createElement('select');select.className='people-category-select';categories.concat(row.Category&&!categories.includes(row.Category)?[row.Category]:[]).forEach((v)=>{const o=document.createElement('option');o.value=v;o.textContent=v;select.append(o);});select.value=row.Category||inferPeopleCategory(row.Role);select.addEventListener('change',()=>{row.Category=select.value;markSheetDirty('people');});td.append(select);tr.append(td);return;
          }
          if(header==='Role'){
            const td=document.createElement('td');td.className='role-multi-cell';const roleBtn=button(row.Role||'Choose roles…','role-picker-button');roleBtn.title=row.Role||'Choose one or more roles';roleBtn.addEventListener('click',()=>openPersonRolesPicker(rowIndex));td.append(roleBtn);tr.append(td);return;
          }
          renderTextCell(tr,row,rowIndex,header);
        });
        preferredContribution.concat(extraHeaders).forEach((header)=>renderTextCell(tr,row,rowIndex,header));
        const imageTd=document.createElement('td');imageTd.className='person-image-cell';const imageControls=div('person-image-controls'),imageSelect=document.createElement('select');imageSelect.className='people-image-select';const blank=document.createElement('option');blank.value='';blank.textContent='Default face';imageSelect.append(blank);images.forEach((asset)=>{const o=document.createElement('option');o.value=asset.path;o.textContent=fileNameFromPath(asset.path);imageSelect.append(o);});if(row.Image&&!images.some((a)=>a.path===row.Image)){const o=document.createElement('option');o.value=row.Image;o.textContent=row.Image;imageSelect.append(o);}imageSelect.value=row.Image||'';imageSelect.addEventListener('change',()=>{row.Image=imageSelect.value;markSheetDirty('people');renderPeopleSpreadsheet();});const upload=button('Upload','button ghost');upload.addEventListener('click',()=>uploadPersonFace(rowIndex));imageControls.append(imageSelect,upload);imageTd.append(imageControls);tr.append(imageTd);
        const visTd=document.createElement('td'),vis=document.createElement('select');vis.className='people-visible-select';['true','false'].forEach((v)=>{const o=document.createElement('option');o.value=v;o.textContent=v;vis.append(o);});vis.value=normalizeBooleanString(row.Visible);vis.addEventListener('change',()=>{row.Visible=vis.value;markSheetDirty('people');});visTd.append(vis);tr.append(visTd);
        const action=document.createElement('td');action.className='action-cell';const copy=button('Copy','button ghost'),del=button('Delete','button ghost');copy.addEventListener('click',()=>{const clone=Object.create(null);sheet.headers.forEach((h)=>clone[h]=row[h]||'');sheet.rows.splice(rowIndex+1,0,clone);markSheetDirty('people');renderPeopleSpreadsheet();});del.addEventListener('click',async()=>{const personName=[row['First Name'],row['Last Name']].filter(Boolean).join(' ')||('person '+(rowIndex+1));if(!await totemConfirm('Delete person','Delete '+personName+' from People?','Delete',{danger:true}))return;sheet.rows.splice(rowIndex,1);markSheetDirty('people');resetDocumentSelections();renderPeopleSpreadsheet();});action.append(copy,del);tr.append(action);
        Array.from(tr.children).forEach((cell,index)=>{cell.dataset.label=index===0?'#':(visibleHeaders[index-1]||'');});tbody.append(tr);
      });table.append(tbody);scroll.append(table);if(!tbody.children.length)scroll.append(div('sheet-empty','No people match the filter.'));
    };
    add.addEventListener('click',()=>{const row=Object.create(null);sheet.headers.forEach((h)=>row[h]='');row.Category='Speaker';row.Visible='true';sheet.rows.push(row);markSheetDirty('people');resetDocumentSelections();renderPeopleSpreadsheet();});clearPeople.addEventListener('click',async()=>{if(!sheet.rows.length){toast('People is already empty.','success');return;}const count=sheet.rows.length;const ok=await totemConfirm('Clear People','Remove all '+count+' people from the current People list? Headers and role definitions will be kept.','Clear People',{danger:true});if(!ok)return;sheet.rows.splice(0,sheet.rows.length);markSheetDirty('people');resetDocumentSelections();renderPeopleSpreadsheet();toast('People list cleared. Save people.csv to persist the change.','success');log('info','people.cleared',{rows:count});});search.addEventListener('input',renderTable);catFilter.addEventListener('change',renderTable);renderTable();
  }

  function openPersonRolesPicker(rowIndex) {
    const row = state.people.rows[rowIndex]; if (!row) return;
    const roles = peopleRoles();
    const selected = new Set(String(row.Role || '').split(/[;,|]/).map((x) => x.trim()).filter(Boolean));
    els.modalTitle.textContent = 'Roles · ' + ([row['First Name'], row['Last Name']].filter(Boolean).join(' ') || ('Person ' + (rowIndex + 1)));
    els.modalBody.replaceChildren();
    const list = div('role-picker-list');
    roles.forEach((role) => {
      const label = document.createElement('label'); label.className = 'role-check';
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.has(role);
      input.addEventListener('change', () => { if (input.checked) selected.add(role); else selected.delete(role); });
      const text = document.createElement('span'); text.textContent = role; label.append(input, text); list.append(label);
    });
    const actions = div('inline-actions'); const apply = button('Apply roles','button primary'); const manage = button('Manage roles','button ghost');
    apply.addEventListener('click', () => { row.Role = Array.from(selected).join('; '); if (!row.Category) row.Category = inferPeopleCategory(row.Role); markSheetDirty('people'); closeModal(); renderPeopleSpreadsheet(); });
    manage.addEventListener('click', openRoleManager);
    actions.append(apply, manage); els.modalBody.append(list, actions); els.modalBackdrop.classList.remove('hidden');
  }

  function openRoleManager() {
    const current = peopleRoles();
    els.modalTitle.textContent = 'Manage people roles'; els.modalBody.replaceChildren();
    const list = div('role-manager-list');
    const rows = current.map((role) => ({ original: role, value: role }));
    const render = () => {
      list.replaceChildren();
      rows.forEach((item, index) => {
        const row = div('role-manager-row'); const input = document.createElement('input'); input.value = item.value;
        input.addEventListener('input', () => { item.value = input.value; });
        const del = button('Delete','button ghost'); del.addEventListener('click', () => { rows.splice(index,1); render(); });
        row.append(input, del); list.append(row);
      });
    };
    render();
    const actions = div('inline-actions'); const add = button('Add role','button'); const save = button('Save roles','button primary');
    add.addEventListener('click', () => { rows.push({ original:'', value:'New role' }); render(); });
    save.addEventListener('click', () => { applyPeopleRoles(rows); closeModal(); renderPeopleSpreadsheet(); });
    actions.append(add,save); els.modalBody.append(list,actions); els.modalBackdrop.classList.remove('hidden');
  }

  function openPersonImagePicker(rowIndex) {
    const row = state.people.rows[rowIndex];
    if (!row) return;
    const title = [row['First Name'], row['Last Name']].filter(Boolean).join(' ') || ('Person ' + (rowIndex + 1));
    els.modalTitle.textContent = 'Face image · ' + title;
    els.modalBody.replaceChildren();

    const actions = div('image-picker-actions');
    const upload = button('Upload new image', 'button primary');
    const useDefault = button('Use default face', 'button ghost');
    upload.addEventListener('click', () => { closeModal(); uploadPersonFace(rowIndex); });
    useDefault.addEventListener('click', () => {
      row.Image = '';
      markSheetDirty('people');
      closeModal();
      renderPeopleSpreadsheet();
      log('debug', 'people.face_defaulted', { row: rowIndex + 1 });
    });
    actions.append(upload, useDefault);
    els.modalBody.append(actions);

    const images = peopleImageAssets();
    if (!images.length) {
      els.modalBody.append(div('sheet-empty', 'No images found in assets/people/. Upload one to add it.'));
    } else {
      const grid = div('image-picker-grid');
      images.forEach((asset) => {
        const choice = button('', 'image-picker-card' + (asset.path === row.Image ? ' active' : ''));
        const image = document.createElement('img'); image.src = assetPreviewUrl(asset.path); image.alt = fileNameFromPath(asset.path);
        const name = div('image-picker-name', fileNameFromPath(asset.path));
        choice.append(image, name);
        choice.addEventListener('click', () => {
          row.Image = asset.path;
          markSheetDirty('people');
          closeModal();
          renderPeopleSpreadsheet();
          log('debug', 'people.face_selected', { row: rowIndex + 1, asset: asset.path });
        });
        grid.append(choice);
      });
      els.modalBody.append(grid);
    }
    els.modalBackdrop.classList.remove('hidden');
  }

  async function uploadPersonFace(rowIndex) {
    const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/webp,image/gif';input.hidden=true;document.body.append(input);
    input.addEventListener('change',async()=>{const file=input.files&&input.files[0];input.remove();if(!file)return;try{const row=state.people.rows[rowIndex];const base=slugify([row['First Name'],row['Last Name']].filter(Boolean).join('-')||('person-'+(rowIndex+1)));const ext=extensionOf(file.name)||'jpg';const path='assets/people/'+base+'.'+ext;await writeProjectBlob(path,file);row.Image=path;markSheetDirty('people');await loadAssets();renderPeopleSpreadsheet();log('info','people.face_added',{row:rowIndex+1,path,bytes:file.size});toast('Face image linked: '+path,'success');}catch(error){log('error','people.face_failed',{message:error.message});toast('Could not add face image: '+error.message,'error');}});input.click();
  }

  function slugify(value){return String(value||'person').normalize('NFKD').replace(/[^A-Za-z0-9]+/g,'-').replace(/^-+|-+$/g,'').toLowerCase().slice(0,80)||'person';}

  function normalizeBooleanString(value) {
    const text = String(value == null ? '' : value).toLowerCase();
    return ['true','1','yes','y'].includes(text) ? 'true' : 'false';
  }

  function handleSpreadsheetPaste(event) {
    const text = event.clipboardData && event.clipboardData.getData('text/plain');
    if (!text || (!text.includes('\t') && !/[\r\n]/.test(text))) return;
    const input = event.currentTarget;
    const kind = input.dataset.kind;
    const sheet = state[kind];
    const startRow = Number(input.dataset.row);
    const startCol = Number(input.dataset.col);
    const matrix = text.replace(/\r/g, '').split('\n').filter((line, index, arr) => !(index === arr.length - 1 && line === '')).map((line) => line.split('\t'));
    if (!matrix.length) return;
    event.preventDefault();
    while (sheet.rows.length < startRow + matrix.length) {
      const row = Object.create(null); sheet.headers.forEach((header) => { row[header] = /^(Visible|Visibile)$/i.test(header) ? 'true' : ''; }); sheet.rows.push(row);
    }
    matrix.forEach((cells, r) => {
      cells.forEach((value, c) => {
        const header = sheet.headers[startCol + c];
        if (header) sheet.rows[startRow + r][header] = value;
      });
    });
    markSheetDirty(kind);
    renderSpreadsheet(kind);
    log('debug', 'spreadsheet.paste_grid', { kind, rows: matrix.length, columns: Math.max(...matrix.map((row) => row.length)) });
  }

  function markSheetDirty(kind) {
    state[kind].dirty = true;
    updateSaveState();
    if (kind === 'people') els.savePeopleBtn.disabled = false;
    if (kind === 'program') els.saveProgramBtn.disabled = false;
  }

  function exportSheet(kind) {
    const sheet = state[kind];
    const text = window.CsvUtil.serialize(sheet.rows, sheet.headers, { bom: false, eol: '\r\n', finalEol: true, protectFormulae: false });
    downloadText(fileNameFromPath(sheet.path), text, 'text/csv;charset=utf-8');
    log('info', 'csv.exported', { kind, rows: sheet.rows.length });
  }

  async function exportSheetXlsx(kind) {
    const sheet = state[kind];
    try {
      const headers = kind === 'program' ? sheet.headers.filter((h) => !isTechnicalProgramHeader(h)) : sheet.headers;
      const blob = await window.XlsxLite.build(sheet.rows, headers, { sheetName: kind === 'people' ? 'People' : 'Program' });
      downloadBlob(fileNameFromPath(sheet.path).replace(/\.csv$/i, '') + '.xlsx', blob);
      log('info', 'xlsx.exported', { kind, rows: sheet.rows.length, columns: headers.length });
    } catch (error) {
      log('error', 'xlsx.export_failed', { kind, message: error.message });
      toast('Excel export failed: ' + error.message, 'error');
    }
  }

  async function saveSheet(kind) {
    const sheet = state[kind];
    try {
      const text = window.CsvUtil.serialize(sheet.rows, sheet.headers, { bom: false, eol: '\r\n', finalEol: true, protectFormulae: false });
      const result = await writeProjectText(sheet.path, text);
      sheet.dirty = false;
      if (kind === 'people' && state.peopleDemo.active) {
        state.peopleDemo.active = false;
        state.peopleDemo.snapshot = null;
        log('info', 'people.demo_committed', { rows: sheet.rows.length, path: sheet.path });
        renderPeopleSpreadsheet();
      }
      updateSaveState();
      renderOverview();
      log('info', 'csv.' + result, { kind, rows: sheet.rows.length, path: sheet.path });
      toast(result === 'saved' ? fileNameFromPath(sheet.path) + ' saved.' : fileNameFromPath(sheet.path) + ' staged for ZIP export.', 'success');
    } catch (error) {
      log('error', 'csv.save_failed', { kind, message: error.message });
      toast('Could not save ' + kind + ': ' + error.message, 'error');
      throw error;
    }
  }

  async function loadAssets() {
    if (!state.config) return;
    clearObjectUrls();
    try {
      if (state.mode === 'fs') state.assets = await collectFsAssets(state.projectHandle);
      else state.assets = collectMemoryAssets();
      renderAssets();
      renderPeopleSpreadsheet();
      if (state.activeSettingsSection) renderSettingsSection();
      renderOverview();
      log('debug', 'assets.loaded', { count: state.assets.length });
    } catch (error) {
      state.assets = [];
      renderAssets();
      if (state.activeSettingsSection) renderSettingsSection();
      log('error', 'assets.load_failed', { message: error.message });
      toast('Could not read assets: ' + error.message, 'error');
    }
  }

  async function collectFsAssets(projectHandle) {
    let assetsDir;
    try { assetsDir = await projectHandle.getDirectoryHandle('assets'); }
    catch (_) { return []; }
    const result = [];
    async function walk(dir, prefix) {
      for await (const [name, handle] of dir.entries()) {
        const path = prefix + name;
        if (handle.kind === 'directory') await walk(handle, path + '/');
        else {
          const file = await handle.getFile();
          result.push({ path: 'assets/' + path, name, blob: file, size: file.size, type: file.type || typeFromName(name), handle });
        }
      }
    }
    await walk(assetsDir, '');
    return result.sort((a, b) => a.path.localeCompare(b.path));
  }

  function collectMemoryAssets() {
    const prefix = state.projectPrefix + 'assets/';
    const result = [];
    state.memoryFiles.forEach((file, path) => {
      if (!path.startsWith(prefix) || state.memoryDeleted.has(path)) return;
      const projectPath = path.slice(state.projectPrefix.length);
      const blob = state.memoryOverrides.get(state.projectPrefix + projectPath) || file;
      result.push({ path: projectPath, name: fileNameFromPath(projectPath), blob, size: blob.size, type: blob.type || typeFromName(projectPath) });
    });
    state.memoryOverrides.forEach((blob, key) => {
      if (!key.startsWith(state.projectPrefix + 'assets/') || state.memoryDeleted.has(key)) return;
      const projectPath = key.slice(state.projectPrefix.length);
      const exists = result.some((item) => item.path === projectPath);
      if (!exists) result.push({ path: projectPath, name: fileNameFromPath(projectPath), blob, size: blob.size, type: blob.type || typeFromName(projectPath) });
    });
    return result.sort((a, b) => a.path.localeCompare(b.path));
  }

  function renderAssets() {
    els.assetGrid.replaceChildren();
    const query = (els.assetSearch.value || '').trim().toLowerCase();
    const assets = state.assets.filter((asset) => !query || asset.path.toLowerCase().includes(query));
    if (!assets.length) {
      els.assetGrid.append(div('sheet-empty', query ? 'No assets match the filter.' : 'No assets found.'));
      return;
    }
    assets.forEach((asset) => {
      const card = div('asset-card'); card.dataset.assetPath=asset.path;
      const preview = div('asset-preview');
      const ext = extensionOf(asset.path);
      if (PREVIEWABLE_EXTENSIONS.has(ext)) {
        const img = document.createElement('img');
        img.src = assetPreviewUrl(asset.path); img.alt = asset.name; preview.append(img);
      } else {
        preview.append(div('asset-file-icon', ext ? ext.toUpperCase() : 'FILE'));
      }
      const body = div('asset-body');
      body.append(div('asset-path', asset.path), div('asset-meta', formatBytes(asset.size) + (asset.type ? ' · ' + asset.type : '')));
      const actions = div('asset-actions');
      const replaceBtn = button('Replace', 'button');
      const downloadBtn = button('Download', 'button ghost');
      const removeBtn = button('Remove', 'button ghost danger');
      replaceBtn.addEventListener('click', () => chooseReplacementForAsset(asset.path));
      downloadBtn.addEventListener('click', () => downloadBlob(asset.name, asset.blob));
      removeBtn.addEventListener('click', () => removeAsset(asset.path));
      actions.append(replaceBtn, downloadBtn, removeBtn); body.append(actions); card.append(preview, body); els.assetGrid.append(card);
    });
  }

  function clearObjectUrls() {
    state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
    state.objectUrls = [];
    state.previewUrls.forEach((url) => { try { URL.revokeObjectURL(url); } catch (_) {} });
    state.previewUrls = [];
    if (state.previewWindow && !state.previewWindow.closed) { try { state.previewWindow.close(); } catch (_) {} }
    state.previewWindow = null;
  }

  function chooseReplacementForAsset(path) {
    els.assetTargetPath.value = path;
    els.assetFileInput.click();
  }

  const TEXT_REFERENCE_EXTENSIONS = new Set(['html','htm','css','js','mjs','json','yaml','yml','csv','md','txt','xml','php','ini','conf','svg']);
  const CLEANUP_PROTECTED_ASSET_NAMES = new Set(['.gitkeep','README.md','IMAGE_CREDITS.md']);

  function replaceStringDeep(value, from, to) {
    let changed = 0;
    function walk(node) {
      if (typeof node === 'string') {
        if (!node.includes(from)) return node;
        changed += node.split(from).length - 1;
        return node.split(from).join(to);
      }
      if (Array.isArray(node)) { node.forEach((item,index)=>{ node[index]=walk(item); }); return node; }
      if (node && typeof node === 'object') { Object.keys(node).forEach((key)=>{ node[key]=walk(node[key]); }); }
      return node;
    }
    walk(value);
    return changed;
  }

  async function replaceAssetReferences(oldPath, newPath) {
    if (!oldPath || !newPath || oldPath === newPath) return 0;
    const configChanged = replaceStringDeep(state.config, oldPath, newPath);
    const regformChanged = state.regformConfig ? replaceStringDeep(state.regformConfig, oldPath, newPath) : 0;
    const peopleChanged = replaceStringDeep(state.people.rows, oldPath, newPath);
    const programChanged = replaceStringDeep(state.program.rows, oldPath, newPath);
    const changed = configChanged + regformChanged + peopleChanged + programChanged;
    if (configChanged) {
      state.yamlText = window.YamlLite.stringify(conferenceConfigForStorage());
      state.yamlDirty = true;
      els.yamlEditor.value = state.yamlText;
    }
    if (regformChanged) {
      state.regformConfig = getConfig('registration.form', state.regformConfig || Object.create(null));
      state.regformYamlText = stringifyRegformSettings(state.regformConfig);
      state.regformDirty = true;
    }
    if (peopleChanged) state.people.dirty = true;
    if (programChanged) state.program.dirty = true;
    if (changed) updateSaveState();
    return changed;
  }

  async function assetReferenceReport(assetPath) {
    const refs = [];
    const entries = await collectProjectEntriesForExport();
    for (const entry of entries) {
      if (!entry || !entry.path || entry.path === assetPath || entry.path.startsWith('assets/')) continue;
      const ext = extensionOf(entry.path);
      if (!TEXT_REFERENCE_EXTENSIONS.has(ext)) continue;
      try {
        const text = await entry.data.text();
        if (text.includes(assetPath)) refs.push(entry.path);
      } catch (_) {}
    }
    return Array.from(new Set(refs)).sort();
  }

  async function removeAsset(path, options) {
    const opts = options || {};
    const assetPath = normalizeProjectPath(path);
    try {
      const refs = opts.references || await assetReferenceReport(assetPath);
      if (!opts.skipConfirm) {
        const detail = refs.length
          ? 'This file is referenced by '+refs.length+' project file'+(refs.length===1?'':'s')+':\n\n'+refs.slice(0,6).join('\n')+(refs.length>6?'\n…':'')+'\n\nRemoving it may leave a broken image or document reference.'
          : 'This asset does not appear to be referenced by the current project files.';
        const ok = await totemConfirm('Remove asset', fileNameFromPath(assetPath)+'\n\n'+detail, 'Remove file', { danger:true });
        if (!ok) return false;
      }
      const result = await deleteProjectPath(assetPath);
      await loadAssets();
      log('info','asset.removed',{path:assetPath,references:refs.length,mode:result});
      if (!opts.silent) toast(result === 'deleted' ? 'Asset removed: '+assetPath : 'Asset marked for removal from the next ZIP: '+assetPath, 'success');
      return true;
    } catch (error) {
      log('error','asset.remove_failed',{path:assetPath,message:error.message});
      if (!opts.silent) toast('Could not remove asset: '+error.message,'error');
      return false;
    }
  }

  async function cleanUnusedAssets() {
    if (!state.config) return;
    const buttonEl = els.cleanUnusedAssetsBtn;
    const oldLabel = buttonEl ? buttonEl.textContent : '';
    if (buttonEl) { buttonEl.disabled = true; buttonEl.textContent = 'Checking…'; }
    try {
      const unused = [];
      for (const asset of state.assets) {
        if (CLEANUP_PROTECTED_ASSET_NAMES.has(fileNameFromPath(asset.path))) continue;
        const refs = await assetReferenceReport(asset.path);
        if (!refs.length) unused.push(asset.path);
      }
      if (!unused.length) { toast('No unused assets found.','success'); return; }
      const preview = unused.slice(0,12).join('\n') + (unused.length>12?'\n… and '+(unused.length-12)+' more':'' );
      const ok = await totemConfirm('Clean unused assets', 'The editor found '+unused.length+' asset file'+(unused.length===1?'':'s')+' with no explicit reference in the conference files.\n\n'+preview+'\n\nProtected metadata files such as .gitkeep, README.md and IMAGE_CREDITS.md are never included. Remove these unused assets?', 'Remove '+unused.length+' unused', {danger:true});
      if (!ok) return;
      let removed = 0;
      for (const path of unused) if (await removeAsset(path,{skipConfirm:true,silent:true,references:[]})) removed += 1;
      await loadAssets();
      log('info','assets.cleaned',{found:unused.length,removed});
      toast('Removed '+removed+' unused asset'+(removed===1?'':'s')+'.','success');
    } catch (error) {
      log('error','assets.clean_failed',{message:error.message,stack:error.stack||''});
      toast('Could not clean unused assets: '+error.message,'error');
    } finally {
      if (buttonEl) { buttonEl.disabled = !state.config; buttonEl.textContent = oldLabel || 'Clean unused'; }
    }
  }

  async function handleAddOrReplaceAsset() {
    const file = els.assetFileInput.files && els.assetFileInput.files[0];
    els.assetFileInput.value = '';
    if (!file) return;
    let target = (els.assetTargetPath.value || '').trim();
    if (!target) target = 'assets/' + file.name;
    try {
      target = normalizeProjectPath(target);
      if (!target.startsWith('assets/')) throw new Error('Asset path must be inside assets/');
      const existing = state.assets.find((asset)=>asset.path===target);
      let finalTarget = target;
      let oldTarget = '';
      if (existing) {
        oldTarget = target;
        const oldExt = extensionOf(target), newExt = extensionOf(file.name);
        if (oldExt && newExt && oldExt !== newExt) {
          finalTarget = target.replace(/\.[^/.]+$/, '') + '.' + newExt;
          const conflict = state.assets.some((asset)=>asset.path===finalTarget && asset.path!==oldTarget);
          if (conflict) {
            const overwrite = await totemConfirm('Replace existing asset', 'The new file type changes the asset path to '+finalTarget+', but that file already exists. Replace it?', 'Replace', {danger:true});
            if (!overwrite) return;
          }
        }
      }
      const result = await writeProjectBlob(finalTarget, file);
      let updatedRefs = 0;
      if (oldTarget && finalTarget !== oldTarget) {
        updatedRefs = await replaceAssetReferences(oldTarget, finalTarget);
        await deleteProjectPath(oldTarget);
      }
      els.assetTargetPath.value = finalTarget;
      await loadAssets();
      if (state.activeView === 'documents') renderDocuments();
      log('info', 'asset.' + result, { path: finalTarget, replaced: oldTarget || null, updated_references: updatedRefs, bytes: file.size });
      const suffix = oldTarget && finalTarget !== oldTarget ? ' File type changed; '+updatedRefs+' reference'+(updatedRefs===1?'':'s')+' updated.' : '';
      toast((result === 'saved' ? 'Asset saved: ' : 'Asset staged for ZIP export: ') + finalTarget + suffix, 'success');
    } catch (error) {
      log('error', 'asset.write_failed', { path: target, message: error.message });
      toast('Could not write asset: ' + error.message, 'error');
    }
  }


  function markVersionFormDirty() {
    if (!state.config) return;
    const candidate = String(els.releaseVersion.value || '').trim();
    if (/^\d+(?:\.\d+){0,2}$/.test(candidate)) {
      try { state.version.version = normalizeSemver(candidate); } catch (_) {}
    }
    state.version.status = els.releaseStatus.value || state.version.status || 'draft';
    state.versionDirty = true;
    updateSaveState();
  }

  function normalizeSemver(value) {
    const text = String(value || '').trim();
    if (/^\d+\.\d+\.\d+$/.test(text)) return text;
    if (/^\d+\.\d+$/.test(text)) return text + '.0';
    if (/^\d+$/.test(text)) return text + '.0.0';
    throw new Error('Version must use semantic versioning, for example 1.6.0');
  }

  function bumpVersion(part) {
    try {
      const current = normalizeSemver(els.releaseVersion.value || state.version.version || '1.0.0').split('.').map(Number);
      if (part === 'major') { current[0] += 1; current[1] = 0; current[2] = 0; }
      else if (part === 'minor') { current[1] += 1; current[2] = 0; }
      else current[2] += 1;
      const next = current.join('.');
      state.version.version = next;
      state.version.status = els.releaseStatus.value || state.version.status || 'draft';
      state.version.updated_at = new Date().toISOString();
      state.version.history.unshift({ version: next, status: state.version.status, at: state.version.updated_at, note: String(els.releaseNote.value || '').trim() || (part + ' version bump') });
      state.version.history = state.version.history.slice(0, 40);
      state.versionDirty = true;
      els.releaseVersion.value = next;
      els.releaseNote.value = '';
      renderVersionPanel(); updateSaveState();
      log('info', 'version.bumped', { project: state.projectName, version: next, part });
    } catch (error) { toast(error.message, 'error'); }
  }

  async function saveVersionMetadata() {
    if (!state.config) return;
    try {
      const next = normalizeSemver(els.releaseVersion.value || state.version.version || '1.0.0');
      const note = String(els.releaseNote.value || '').trim();
      const changed = next !== state.version.version || els.releaseStatus.value !== state.version.status;
      state.version.version = next;
      state.version.status = els.releaseStatus.value || 'draft';
      state.version.updated_at = new Date().toISOString();
      if (changed || note) {
        state.version.history.unshift({ version: next, status: state.version.status, at: state.version.updated_at, note: note || 'Version metadata updated' });
        state.version.history = state.version.history.slice(0, 40);
      }
      const text = JSON.stringify(state.version, null, 2) + '\n';
      const result = await writeProjectText('conference.version.json', text);
      state.versionDirty = false; els.releaseNote.value = '';
      renderVersionPanel(); renderOverview(); updateSaveState();
      log('info', 'version.' + result, { version: next, status: state.version.status });
      toast(result === 'saved' ? 'Version metadata saved.' : 'Version metadata staged for ZIP export.', 'success');
    } catch (error) {
      log('error', 'version.save_failed', { message: error.message }); toast('Could not save version: ' + error.message, 'error'); throw error;
    }
  }

  function renderVersionPanel() {
    if (!state.config || !els.releaseVersion) return;
    els.releaseVersion.value = state.version.version || '1.0.0';
    els.releaseStatus.value = state.version.status || 'draft';
    els.releaseVersionLabel.textContent = 'v' + (state.version.version || '1.0.0');
    els.versionHistory.replaceChildren();
    (state.version.history || []).slice(0, 6).forEach((entry) => {
      const row = div('version-row');
      const version = document.createElement('b'); version.textContent = 'v' + String(entry.version || '');
      const status = document.createElement('span'); status.textContent = String(entry.status || '');
      const note = document.createElement('span'); note.textContent = String(entry.note || entry.at || '');
      row.append(version, status, note); els.versionHistory.append(row);
    });
    if (!state.version.history || !state.version.history.length) els.versionHistory.append(div('sheet-empty', 'No version history yet.'));
  }

  function assertNoServerMailTransportInWebsiteConfig() {
    const sources = [
      ['conference.yaml', state.yamlText || ''],
      ['regform/settings.yaml', state.regformYamlText || stringifyRegformSettings(state.regformConfig)]
    ];
    const forbidden = /^(?:\s*)(smtp_(?:host|port|security|username|password|token)|mail_transport|sendmail_path)\s*:/im;
    for (const [path, text] of sources) {
      const match = String(text || '').match(forbidden);
      if (match) {
        const key = match[1] || 'SMTP transport setting';
        throw new Error(path + ' contains server-side mail transport key "' + key + '". WEBSITE packages may contain recipients/from addresses, but SMTP host/user/password/transport must be configured on the hosting server.');
      }
    }
  }

  async function exportProjectZip() {
    if (!state.config) return;
    if (state.sectionYamlDirty && !applySectionYamlFromEditor()) return;
    const buttonEl = els.exportZipBtn;
    const old = buttonEl.textContent; buttonEl.disabled = true; buttonEl.textContent = 'Building ZIP…';
    try {
      assertNoServerMailTransportInWebsiteConfig();
      const entries = await collectProjectEntriesForExport();
      await assertPublishableWebsiteEntries(entries);
      const version = normalizeSemver(els.releaseVersion.value || state.version.version || '1.0.0');
      const blob = await window.ZipLite.createBlob(entries, { root: state.projectName });
      const filename = exportConferenceStem() + '_WEBSITE.zip';
      downloadBlob(filename, blob);
      log('info', 'project.zip_exported', { project: state.projectName, version, files: entries.length, bytes: blob.size });
      toast('Complete ZIP exported: ' + filename, 'success');
    } catch (error) {
      log('error', 'project.zip_failed', { message: error.message }); toast('ZIP export failed: ' + error.message, 'error');
    } finally { buttonEl.textContent = old; buttonEl.disabled = false; }
  }

  function mifpExportInput() {
    return {
      projectName: state.projectName,
      config: state.config,
      reviewStatus: (els.releaseStatus && els.releaseStatus.value) || state.version.status || 'draft',
      conferenceVersion: (els.releaseVersion && els.releaseVersion.value) || state.version.version || '',
      editorVersion: EDITOR_VERSION,
      assets: state.assets || []
    };
  }

  async function buildMifpExportModel() {
    if (!state.config) throw new Error('Open a conference before creating a MIFP export.');
    if (!window.MifpExport || typeof window.MifpExport.build !== 'function') throw new Error('MIFP export module is not available. Reload the editor.');
    return window.MifpExport.build(mifpExportInput());
  }

  function mifpDefinitionList(items) {
    const list = document.createElement('dl');
    list.className = 'definition-list mifp-definition-list';
    (items || []).forEach(([term, value]) => {
      const dt = document.createElement('dt'); dt.textContent = term;
      const dd = document.createElement('dd'); dd.textContent = value || '—';
      list.append(dt, dd);
    });
    return list;
  }

  async function renderMifpExport() {
    if (!els.mifpExportEditor) return;
    const token = ++state.mifpExportPreviewToken;
    els.mifpExportEditor.replaceChildren();
    if (!state.config) {
      const empty = div('empty-state compact');
      const title = document.createElement('h2'); title.textContent = 'No conference loaded';
      const text = document.createElement('p'); text.textContent = 'Open a conference to build the MIFP webapp export.';
      empty.append(title, text); els.mifpExportEditor.append(empty); return;
    }

    const loading = div('panel mifp-export-loading');
    loading.append(div('eyebrow', 'MIFP package'), Object.assign(document.createElement('h2'), { textContent: 'Building export preview…' }));
    els.mifpExportEditor.append(loading);

    try {
      const model = await buildMifpExportModel();
      if (token !== state.mifpExportPreviewToken) return;
      els.mifpExportEditor.replaceChildren();

      const stats = div('stat-grid');
      [
        ['Format', 'mifp-content v1', 'Strict webapp transport'],
        ['Records', String(model.manifest.records), '1 canonical event'],
        ['Assets', String(model.manifest.files.length), 'Curated packaged files'],
        ['Status', model.record.data.review_status || 'draft', 'From conference release status']
      ].forEach(([label, value, note]) => {
        const card = div('stat-card');
        card.append(div('stat-label', label), div('stat-value', value), div('stat-note', note));
        stats.append(card);
      });
      els.mifpExportEditor.append(stats);

      const grid = div('panel-grid mifp-export-grid');
      const eventPanel = div('panel');
      const eventHead = div('panel-head');
      const eventCopy = document.createElement('div'); eventCopy.innerHTML = '<div class="eyebrow">Canonical mapping</div><h2>Event record</h2>';
      eventHead.append(eventCopy); eventPanel.append(eventHead);
      const data = model.record.data;
      eventPanel.append(mifpDefinitionList([
        ['UID', data.uid], ['Slug', data.slug], ['Title', data.title],
        ['Date', [data.start_date || '', data.end_date && data.end_date !== data.start_date ? data.end_date : ''].filter(Boolean).join(' → ') || data.date_text],
        ['Precision', data.date_precision], ['Location', data.location], ['Series', data.series_key], ['Public URL', data.remote_url]
      ]));

      const assetsPanel = div('panel');
      const assetsHead = div('panel-head');
      const assetsCopy = document.createElement('div'); assetsCopy.innerHTML = '<div class="eyebrow">Portable files</div><h2>Packaged assets</h2>';
      assetsHead.append(assetsCopy); assetsPanel.append(assetsHead);
      if (!model.assets.length) assetsPanel.append(div('sheet-empty', 'No local cover/logo/document asset is configured for MIFP export. The package is still valid.'));
      else {
        const list = div('mifp-asset-list');
        model.assets.forEach((asset) => {
          const row = div('mifp-asset-row');
          const main = document.createElement('div');
          const strong = document.createElement('b'); strong.textContent = asset.caption || asset.original_filename || asset.filename;
          const meta = document.createElement('span'); meta.textContent = asset.role + ' · ' + asset.kind + ' · ' + formatBytes(asset.size || 0);
          main.append(strong, meta);
          const path = document.createElement('code'); path.textContent = asset.path;
          row.append(main, path); list.append(row);
        });
        assetsPanel.append(list);
      }
      grid.append(eventPanel, assetsPanel);
      els.mifpExportEditor.append(grid);

      if (model.warnings.length) {
        const warningPanel = div('panel mifp-warning-panel');
        const head = document.createElement('div'); head.innerHTML = '<div class="eyebrow">Preflight</div><h2>Warnings</h2><p>The export remains schema-valid, but these source values were omitted or need review.</p>';
        const ul = document.createElement('ul'); ul.className = 'mifp-warning-list';
        model.warnings.forEach((warning) => { const li = document.createElement('li'); li.textContent = warning; ul.append(li); });
        warningPanel.append(head, ul); els.mifpExportEditor.append(warningPanel);
      }

      const note = div('panel mifp-export-note');
      note.innerHTML = '<div class="eyebrow">Schema boundary</div><h2>People and program</h2><p>The current MIFP <code>event</code> schema does not accept speakers, chairs, committee or participants inside <code>event.data</code>. They remain in the conference website files and are intentionally excluded from this strict v1 transport package.</p>';
      els.mifpExportEditor.append(note);

      const previewPanel = div('panel');
      const previewHead = div('panel-head');
      const previewCopy = document.createElement('div'); previewCopy.innerHTML = '<div class="eyebrow">records.jsonl</div><h2>Generated JSONL</h2><p>Exactly one independently parseable JSON object followed by a newline.</p>';
      previewHead.append(previewCopy);
      const pre = document.createElement('pre'); pre.className = 'mifp-json-preview'; pre.textContent = JSON.stringify(model.record, null, 2);
      previewPanel.append(previewHead, pre); els.mifpExportEditor.append(previewPanel);
    } catch (error) {
      if (token !== state.mifpExportPreviewToken) return;
      els.mifpExportEditor.replaceChildren();
      const panel = div('panel mifp-warning-panel');
      const title = document.createElement('h2'); title.textContent = 'MIFP export cannot be built';
      const text = document.createElement('p'); text.textContent = error.message;
      panel.append(title, text); els.mifpExportEditor.append(panel);
      log('error', 'mifp_export.preview_failed', { message: error.message });
    }
  }

  async function exportMifpJsonl() {
    if (!state.config || !els.exportMifpJsonlBtn) return;
    const buttonEl = els.exportMifpJsonlBtn;
    const old = buttonEl.textContent; buttonEl.disabled = true; buttonEl.textContent = 'Building JSONL…';
    try {
      const model = await buildMifpExportModel();
      const filename = exportConferenceStem() + '_INFO.jsonl';
      downloadBlob(filename, new Blob([model.recordsText], { type: 'application/x-ndjson;charset=utf-8' }));
      log('info', 'mifp_export.jsonl_exported', { uid: model.record.data.uid, filename, assets: model.assets.length, warnings: model.warnings.length });
      toast(filename + ' exported.' + (model.warnings.length ? ' Review ' + model.warnings.length + ' warning(s).' : ''), model.warnings.length ? 'warn' : 'success');
    } catch (error) {
      log('error', 'mifp_export.jsonl_failed', { message: error.message });
      toast('MIFP JSONL export failed: ' + error.message, 'error');
    } finally { buttonEl.textContent = old; buttonEl.disabled = !state.config; }
  }

  async function exportMifpZip() {
    if (!state.config || !els.exportMifpZipBtn) return;
    const buttonEl = els.exportMifpZipBtn;
    const old = buttonEl.textContent; buttonEl.disabled = true; buttonEl.textContent = 'Building MIFP ZIP…';
    try {
      const model = await buildMifpExportModel();
      const blob = await window.ZipLite.createBlob(model.entries);
      const filename = exportConferenceStem() + '_INFO.zip';
      downloadBlob(filename, blob);
      log('info', 'mifp_export.zip_exported', { uid: model.record.data.uid, filename, recordsSha256: model.recordsSha256, files: model.manifest.files.length, bytes: blob.size, warnings: model.warnings.length });
      toast(filename + ' exported.' + (model.warnings.length ? ' Review ' + model.warnings.length + ' warning(s).' : ''), model.warnings.length ? 'warn' : 'success');
    } catch (error) {
      log('error', 'mifp_export.zip_failed', { message: error.message });
      toast('MIFP ZIP export failed: ' + error.message, 'error');
    } finally { buttonEl.textContent = old; buttonEl.disabled = !state.config; }
  }

  async function assertPublishableWebsiteEntries(entries) {
    // Producer checks from MIFP_EVENT_IMPORT_PACKAGE_SPEC.md §§2, 8–12.
    // Validate packaged bytes, not UI snapshots. Never silently omit a file.
    if (!/^[A-Za-z0-9][A-Za-z0-9._~-]{0,99}$/.test(state.projectName)) throw new Error('WEBSITE requires one safe event directory (at most 100 characters).');
    const names = new Set();
    const sensitiveNames = new Set(['.env','.htpasswd','.netrc','.npmrc','.pgpass','.s3cfg','credentials.json','credential.json','creds.json','secrets.json','secret.json','tokens.json','token.json','shadow']);
    const sensitiveSuffixes = /\.(?:bak|cer|crt|db|key|old|orig|p12|pem|pfx|sql|sqlite|sqlite3)$/i;
    const storage = String(state.regformConfig?.backend?.storage_path || 'registrations').replace(/\\/g, '/').replace(/\/+$/, '');
    const privateRoots = ['regform/storage/'];
    if (storage !== 'registrations' && storage && !storage.startsWith('/') && !storage.includes('..')) privateRoots.push('regform/' + storage + '/');
    const decode = async (data) => new TextDecoder('utf-8', { fatal:true }).decode(await data.arrayBuffer());
    for (const { path, data } of entries) {
      const parts = path.split('/'), lower = path.toLowerCase(), name = parts.at(-1).toLowerCase();
      if (!path || path.includes('\\') || path.includes('\0') || /^[A-Za-z]:/.test(path) || parts.some((part) => !part || part === '.' || part === '..')) throw new Error('Unsafe WEBSITE path: ' + path);
      if (names.has(path)) throw new Error('Duplicate WEBSITE path: ' + path);
      names.add(path);
      if (parts.some((part) => ['.git','.svn'].includes(part.toLowerCase())) || sensitiveNames.has(name) || name.startsWith('.env.') || /^id_(?:rsa|dsa|ecdsa|ed25519)/.test(name) || sensitiveSuffixes.test(name) || /\.(?:db|sqlite3?)-(?:wal|shm)$/.test(name) || privateRoots.some((root) => lower.startsWith(root.toLowerCase()))) {
        throw new Error('WEBSITE contains private/runtime or prohibited files: ' + path + '. Use a clean source copy. No files were removed.');
      }
      if (lower.startsWith('regform/registrations/')) {
        const relative = lower.slice('regform/registrations/'.length);
        let safe = false;
        if (data.size <= 16384) {
          const text = await decode(data);
          if (['.gitkeep','.keep'].includes(name)) safe = data.size === 0;
          else if (relative === '.gitignore') safe = !text.includes('\0');
          else if (relative === 'index.php') safe = /^\s*<\?php\s+http_response_code\s*\(\s*404\s*\)\s*;\s*exit\s*;?\s*$/i.test(text);
          else if (relative === '.htaccess') {
            const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
            safe = lines.length > 0 && lines.every((line) => /^(?:#.*|<IfModule\s+mod_rewrite\.c>|<\/IfModule>|RewriteEngine\s+On|RewriteRule\s+\^\s+-\s+\[F,L\]|Require\s+all\s+denied|Order\s+allow,deny|Deny\s+from\s+all)$/i.test(line));
          }
        }
        if (!safe) throw new Error('WEBSITE contains private/runtime data or an invalid registration guard: ' + path);
      }
    }
    if (!names.has('conference.yaml') || (!names.has('index.html') && !names.has('index.htm'))) throw new Error('WEBSITE requires conference.yaml and a static root index.html or index.htm.');
    if (entries.filter(({path}) => path.split('/').at(-1).toLowerCase() === 'conference.yaml').length !== 1) throw new Error('WEBSITE requires exactly one conference.yaml.');
    const yaml = entries.find(({path}) => path === 'conference.yaml').data;
    if (yaml.size > 2 * 1024 * 1024) throw new Error('conference.yaml exceeds 2 MiB.');
    const config = window.YamlLite.parse(await decode(yaml));
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('conference.yaml must contain a mapping.');
    const versions = entries.filter(({path}) => path.split('/').at(-1).toLowerCase() === 'conference.version.json');
    if (versions.length > 1) throw new Error('WEBSITE contains multiple conference.version.json files.');
    if (versions.length) {
      const version = JSON.parse(await decode(versions[0].data));
      if (!version || typeof version !== 'object' || Array.isArray(version)) throw new Error('conference.version.json must contain an object.');
    }
  }

  async function collectProjectEntriesForExport(options) {
    const map = new Map();
    if (state.mode === 'fs') {
      async function walk(dir, prefix) {
        for await (const [name, handle] of dir.entries()) {
          const path = prefix + name;
          if (handle.kind === 'directory') await walk(handle, path + '/');
          else map.set(path, await handle.getFile());
        }
      }
      await walk(state.projectHandle, '');
    } else {
      state.memoryFiles.forEach((file, key) => {
        if (!key.startsWith(state.projectPrefix) || state.memoryDeleted.has(key)) return;
        const path = key.slice(state.projectPrefix.length); if (path) map.set(path, file);
      });
      state.memoryOverrides.forEach((blob, key) => {
        if (!key.startsWith(state.projectPrefix) || state.memoryDeleted.has(key)) return;
        const path = key.slice(state.projectPrefix.length); if (path) map.set(path, blob);
      });
    }

    // Export the selected project as-is. Overlay only unsaved edits: parsing a
    // CSV/YAML for the UI is not an edit and must not rewrite the source bytes.
    if (state.yamlDirty) map.set('conference.yaml', new Blob([state.yamlText], { type:'text/yaml' }));
    if (state.regformDirty) map.set('regform/settings.yaml', new Blob([state.regformYamlText || stringifyRegformSettings(state.regformConfig)], { type:'text/yaml' }));
    for (const sheet of [state.people, state.program]) {
      if (sheet.dirty) map.set(sheet.path, new Blob([window.CsvUtil.serialize(sheet.rows, sheet.headers, { bom:false, eol:'\r\n', finalEol:true, protectFormulae:false })], { type:'text/csv' }));
    }
    if (state.versionDirty) {
      const versionCopy = Object.assign({}, state.version, { version: normalizeSemver(els.releaseVersion.value || state.version.version), status: els.releaseStatus.value || state.version.status, updated_at: new Date().toISOString() });
      map.set('conference.version.json', new Blob([JSON.stringify(versionCopy, null, 2) + '\n'], { type:'application/json' }));
    }
    return Array.from(map.entries()).sort((a,b)=>a[0].localeCompare(b[0])).map(([path,data])=>({ path, data }));
  }

  async function createConferenceFromTemplate() {
    if (!supportsFsAccess) {
      toast('Creating a conference requires a browser with local folder write access (Chromium/Chrome/Edge).', 'error');
      return;
    }
    const requested = await totemPrompt(
      'New conference',
      'Create a completely clean conference: reusable site files, placeholder-only conference.yaml, empty People and empty Program. No data is copied from another conference.',
      '',
      {inputLabel:'Folder / acronym',inputPlaceholder:'ICP2DC-2028'}
    );
    if (requested == null) return;
    const name = requested.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$/.test(name)) { toast('Use only letters, numbers, dot, dash and underscore.', 'error'); return; }
    if (['TEMPLATE','NEW-CONFERENCE'].includes(name.toUpperCase())) { toast('Enter the real conference folder/acronym, for example ICP2DC-2028.', 'error'); return; }

    let created = false;
    try {
      let root = rootCanHostConferences() ? state.rootHandle : null;
      if (!root) {
        root = await window.showDirectoryPicker({ mode:'readwrite', id:'mifp-new-conference-parent' });
        state.mode='fs';
        state.rootHandle=root;
        state.workspaceName=root.name;
        state.projectHandle=null;
        state.projectName='';
        state.projectPrefix='';
        state.memoryFiles.clear();
        state.memoryOverrides.clear();
        state.memoryDeleted.clear();
        state.projects=await discoverFsProjects(root);
        updateWorkspaceUi();
        populateProjectPicker();
      } else {
        state.projects=await discoverFsProjects(root);
      }

      try { await root.getDirectoryHandle(name); throw new Error('Folder already exists'); }
      catch (error) { if (error.message === 'Folder already exists') throw error; if (error.name !== 'NotFoundError') throw error; }

      const dest = await root.getDirectoryHandle(name, { create: true });
      created = true;
      const scaffold = 'templates/conference-template.zip';
      const entries = await loadBundledConferenceTemplateEntries();
      await writeTemplateEntriesToDirectory(entries, dest);

      await initializePlaceholderConference(dest, name);
      state.projects = await discoverFsProjects(root);
      populateProjectPicker();
      const project = state.projects.find((item) => item.name === name);
      log('info', 'conference.created_blank', { name, scaffold, placeholderOnly:true });
      toast('Created '+name+': placeholder-only conference, empty People and empty Program.', 'success');
      if (project) await loadProject(project);
    } catch (error) {
      if (created) {
        try { const cleanupRoot=state.rootHandle; if(cleanupRoot) await cleanupRoot.removeEntry(name, { recursive:true }); } catch (_) {}
      }
      log('error', 'conference.create_failed', { name, message: error.message });
      toast('Could not create conference: ' + error.message, 'error');
    }
  }

  async function loadBundledConferenceTemplateEntries() {
    if (location.protocol === 'file:') throw new Error('The bundled conference template cannot be fetched from file://. Serve the editor over local HTTP (for example python -m http.server) to use New conference.');
    const url = new URL('templates/conference-template.zip', document.baseURI);
    const response = await fetch(url, { cache:'no-store', credentials:'same-origin' });
    if (!response.ok) throw new Error('Bundled placeholder template is unavailable (HTTP '+response.status+').');
    const entries = stripBundleRoot(await readZipEntries(await response.blob()));
    if (!entries.has('conference.yaml')) throw new Error('Bundled placeholder template is invalid: conference.yaml is missing.');
    if (!entries.has('regform/settings.yaml')) throw new Error('Bundled placeholder template is invalid: regform/settings.yaml is missing.');
    return entries;
  }

  async function writeTemplateEntriesToDirectory(entries, destination) {
    for (const [path, blob] of entries.entries()) {
      const safe = normalizeProjectPath(path);
      const handle = await getFileHandleByPath(destination, safe, true);
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
    }
  }

  function replaceConferenceFacts(value, replacements) {
    if (typeof value === 'string') {
      let out=value;
      replacements.forEach(([from,to])=>{if(from)out=out.split(from).join(to);});
      return out;
    }
    if (Array.isArray(value)) return value.map((item)=>replaceConferenceFacts(item,replacements));
    if (value && typeof value === 'object') { const out=Object.create(null);Object.keys(value).forEach((key)=>out[key]=replaceConferenceFacts(value[key],replacements));return out; }
    return value;
  }

  async function replacePlaceholderTokenInDirectory(dir, token, replacement) {
    const textual = new Set(['html','css','js','yaml','yml','json','md','txt','php','htaccess','gitignore','xml']);
    async function walk(current) {
      for await (const [entryName, handle] of current.entries()) {
        if (handle.kind === 'directory') { await walk(handle); continue; }
        const ext = extensionOf(entryName) || entryName.replace(/^\./,'').toLowerCase();
        if (!textual.has(ext)) continue;
        const file = await handle.getFile();
        let text;
        try { text = await file.text(); } catch (_) { continue; }
        if (!text.includes(token)) continue;
        const writable = await handle.createWritable();
        await writable.write(text.split(token).join(replacement));
        await writable.close();
      }
    }
    await walk(dir);
  }

  async function initializePlaceholderConference(dest, name) {
    // The source scaffold is already neutral. Replace only the explicit template
    // token; never derive the new conference from the currently opened project.
    await replacePlaceholderTokenInDirectory(dest, 'NEW-CONFERENCE', name);

    const configHandle=await getFileHandleByPath(dest,'conference.yaml',false);
    const originalText=await (await configHandle.getFile()).text();
    let cfg=window.YamlLite.parse(originalText);
    if(!cfg.conference||typeof cfg.conference!=='object')cfg.conference={};
    Object.assign(cfg.conference,{acronym:name,full_name:'TBC',city:'TBC',country:'TBC',venue:'TBC',address:'TBC',timezone:'TBC',email:'TBC',phone:'TBC',contact_name:'TBC',emergency_contact:'TBC',start_date:'',end_date:'',date_label:'TBC'});
    if(cfg.site&&typeof cfg.site==='object'){cfg.site.title=name;cfg.site.short_name=name;cfg.site.year='TBC';cfg.site.base_url='TBC';cfg.site.description='TBC';cfg.site.keywords='TBC';}
    if(cfg.hero&&typeof cfg.hero==='object'){cfg.hero.title=name;cfg.hero.subtitle='TBC';cfg.hero.eyebrow='TBC';cfg.hero.meta='TBC';cfg.hero.description='TBC';}
    if(cfg.committee&&typeof cfg.committee==='object'){cfg.committee.label='People';cfg.committee.title='Committee';cfg.committee.intro='Committee membership for '+name+' is TBC.';delete cfg.committee.members_title;delete cfg.committee.members_role;}
    if(cfg.program&&typeof cfg.program==='object'){
      cfg.program.title=name+' Program';cfg.program.intro='TBC';cfg.program.empty_message='Program TBC.';
      if(!cfg.program.download||typeof cfg.program.download!=='object')cfg.program.download={};
      Object.assign(cfg.program.download,{enabled:true,mode:'generated',label:'Download program PDF',local_file:'assets/documents/program.pdf',local_filename:name+'-program.pdf',generated_filename:name+'-program.pdf'});
      if(!cfg.program.pdf||typeof cfg.program.pdf!=='object')cfg.program.pdf={};
      cfg.program.pdf.filename=name+'-program.pdf';cfg.program.pdf.title=name;cfg.program.pdf.subtitle='TBC';
    }
    if(cfg.important_dates&&typeof cfg.important_dates==='object')cfg.important_dates.items=[{date:'TBC',description:'TBC'}];
    if(cfg.documents&&cfg.documents.certificates&&Array.isArray(cfg.documents.certificates.signatures)){
      cfg.documents.certificates.signatures.forEach((sig)=>{if(sig&&typeof sig==='object'){sig.name='TBC';sig.affiliation='TBC';}});
    }
    const yaml=window.YamlLite.stringify(cfg);
    const writable=await configHandle.createWritable();
    await writable.write(yaml.endsWith('\n')?yaml:yaml+'\n');
    await writable.close();

    const settingsHandle = await getFileHandleByPath(dest, 'regform/settings.yaml', false);
    const settingsDoc = window.YamlLite.parse(await (await settingsHandle.getFile()).text());
    settingsDoc.regform.submit_enabled = false;
    const settingsWriter = await settingsHandle.createWritable();
    await settingsWriter.write(window.YamlLite.stringify(settingsDoc));
    await settingsWriter.close();

    const peopleHeaders=['First Name','Last Name','Category','Role','Affiliation','Country','Presentation Title','Presentation Type','Image','Visible'];
    const programHeaders=['Day','Date','Start Time','End Time','Type','Title','Speaker','Affiliation','Chair','Location','Notes','Visible'];
    const peoplePath=(cfg.runtime&&cfg.runtime.people_csv)||'data/people.csv',programPath=(cfg.runtime&&cfg.runtime.program_csv)||'data/program.csv';
    const ph=await getFileHandleByPath(dest,peoplePath,true),pw=await ph.createWritable();
    await pw.write(window.CsvUtil.serialize([],peopleHeaders,{bom:false,eol:'\r\n',finalEol:true,protectFormulae:false}));await pw.close();
    const gh=await getFileHandleByPath(dest,programPath,true),gw=await gh.createWritable();
    await gw.write(window.CsvUtil.serialize([],programHeaders,{bom:false,eol:'\r\n',finalEol:true,protectFormulae:false}));await gw.close();
    const versionHandle=await getFileHandleByPath(dest,'conference.version.json',true),vw=await versionHandle.createWritable();
    await vw.write(JSON.stringify({schema:1,version:'0.1.0',status:'draft',updated_at:new Date().toISOString(),history:[]},null,2)+'\n');await vw.close();

    // Never carry registration submissions, secrets, rate-limit state or user uploads
    // into a new conference, even if a custom TEMPLATE folder contains them.
    try {
      const reg=await dest.getDirectoryHandle('regform');const storage=await reg.getDirectoryHandle('registrations');
      for await (const [entryName,entryHandle] of storage.entries()) {
        if (['index.php','.htaccess','.gitignore'].includes(entryName)) continue;
        await storage.removeEntry(entryName,{recursive:entryHandle.kind==='directory'});
      }
    } catch (_) {}
  }


  async function copyDirectory(source, destination) {
    for await (const [name, handle] of source.entries()) {
      if (handle.kind === 'directory') {
        const child = await destination.getDirectoryHandle(name, { create: true });
        await copyDirectory(handle, child);
      } else {
        const file = await handle.getFile();
        const out = await destination.getFileHandle(name, { create: true });
        const writable = await out.createWritable();
        await writable.write(file); await writable.close();
      }
    }
  }


  function resetDocumentSelections() {
    state.documentSelections.badges = new Set();
    state.documentSelections.certificates = new Set();
    state.people.rows.forEach((row, index) => {
      if (normalizeBooleanString(row.Visible) !== 'false') {
        state.documentSelections.badges.add(index);
        state.documentSelections.certificates.add(index);
      }
    });
  }

  function parseHumanDate(value) {
    const raw = String(value || '').trim();
    if (!raw || /^TBC$/i.test(raw)) return { iso:'', note:/TBC/i.test(raw)?'TBC':'' };
    const parts = raw.split(/\s*[·|]\s*/);
    const first = parts.shift().trim();
    const note = parts.join(' · ').trim();
    let d = /^\d{4}-\d{2}-\d{2}$/.test(first) ? new Date(first + 'T12:00:00') : new Date(first.replace(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+/i,''));
    if (Number.isNaN(d.getTime())) return { iso:'', note:raw };
    return { iso:[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-'), note };
  }

  function formatImportantDate(iso, note) {
    if (!iso) return note || 'TBC';
    const d = new Date(iso + 'T12:00:00');
    const text = new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'long',year:'numeric'}).format(d);
    return note ? text + ' · ' + note : text;
  }

  function ensureImportantDates() {
    if (!state.config.important_dates || typeof state.config.important_dates !== 'object') state.config.important_dates = { enabled:true, label:'Timeline', title:'Important Dates', items:[] };
    if (!Array.isArray(state.config.important_dates.items)) state.config.important_dates.items = [];
    return state.config.important_dates;
  }

  function renderImportantDates() {
    const host = els.datesEditor; host.replaceChildren();
    if (!state.config) { const e=div('empty-state compact'); e.append(div('', 'No conference loaded')); host.append(e); return; }
    const cfg = ensureImportantDates();
    const shell = div('dates-shell');
    const head = div('panel dates-head-fields');
    const enabledLabel=document.createElement('label'); enabledLabel.textContent='Visible'; const enabled=document.createElement('select'); ['true','false'].forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;enabled.append(o);}); enabled.value=cfg.enabled===false?'false':'true'; enabled.addEventListener('change',()=>{cfg.enabled=enabled.value==='true';syncStructuredYaml('important_dates.enabled',{enabled:cfg.enabled});}); enabledLabel.append(enabled);
    const labelLabel=document.createElement('label');labelLabel.textContent='Section label';const label=document.createElement('input');label.value=cfg.label||'';label.addEventListener('input',()=>{cfg.label=label.value;syncStructuredYaml('important_dates.label',{});});labelLabel.append(label);
    const titleLabel=document.createElement('label');titleLabel.textContent='Title';const title=document.createElement('input');title.value=cfg.title||'Important Dates';title.addEventListener('input',()=>{cfg.title=title.value;syncStructuredYaml('important_dates.title',{});});titleLabel.append(title);head.append(enabledLabel,labelLabel,titleLabel);shell.append(head);
    const toolbar=div('inline-actions');const add=button('Add date','button');const sort=button('Sort by date','button ghost');add.addEventListener('click',()=>{cfg.items.push({date:'TBC',description:'New important date'});syncStructuredYaml('important_dates.item_added',{});renderImportantDates();});sort.addEventListener('click',()=>{cfg.items.sort((a,b)=>{const A=parseHumanDate(a.date).iso||'9999',B=parseHumanDate(b.date).iso||'9999';return A.localeCompare(B);});syncStructuredYaml('important_dates.sorted',{});renderImportantDates();});toolbar.append(add,sort);shell.append(toolbar);
    const list=div('date-list');cfg.items.forEach((item,index)=>{const parsed=parseHumanDate(item.date),row=div('date-row');row.dataset.dateIndex=String(index);row.dataset.configPath='important_dates.items.'+index;const dl=document.createElement('label');dl.textContent='Date';const di=document.createElement('input');di.type='date';di.value=parsed.iso;dl.append(di);const descL=document.createElement('label');descL.textContent='Description';const desc=document.createElement('input');desc.value=item.description||'';descL.append(desc);const noteL=document.createElement('label');noteL.textContent='Note';const note=document.createElement('input');note.value=parsed.note||'';note.placeholder='TBC / provisional';noteL.append(note);const actions=div('date-row-actions');const up=button('↑','button ghost'),down=button('↓','button ghost'),del=button('Delete','button ghost');const apply=()=>{item.date=formatImportantDate(di.value,note.value.trim());item.description=desc.value;syncStructuredYaml('important_dates.changed',{index:index+1});};di.addEventListener('change',apply);note.addEventListener('input',apply);desc.addEventListener('input',apply);up.disabled=index===0;down.disabled=index===cfg.items.length-1;up.addEventListener('click',()=>{[cfg.items[index-1],cfg.items[index]]=[cfg.items[index],cfg.items[index-1]];syncStructuredYaml('important_dates.reordered',{});renderImportantDates();});down.addEventListener('click',()=>{[cfg.items[index+1],cfg.items[index]]=[cfg.items[index],cfg.items[index+1]];syncStructuredYaml('important_dates.reordered',{});renderImportantDates();});del.addEventListener('click',async()=>{if(!await totemConfirm('Delete important date','Delete this important date?','Delete',{danger:true}))return;cfg.items.splice(index,1);syncStructuredYaml('important_dates.deleted',{});renderImportantDates();});actions.append(up,down,del);row.append(dl,descL,noteL,actions);list.append(row);});shell.append(list);host.append(shell);
  }

  async function saveImportantDates() { if (!state.config) return; await saveYaml(); renderImportantDates(); }

  const PLACEHOLDER_RE = /\b(TBC|TBD|TBA|TODO|TO\s+BE\s+(?:CONFIRMED|DETERMINED|ANNOUNCED|DEFINED)|COMING\s+SOON|PLACEHOLDER)\b|\?\?\?/i;

  function placeholderToken(value) {
    const match = String(value == null ? '' : value).match(PLACEHOLDER_RE);
    return match ? String(match[0]).toUpperCase().replace(/\s+/g, ' ') : '';
  }

  function collectPlaceholderIssues() {
    const issues = [];
    function walk(value, parts) {
      if (typeof value === 'string') {
        const token = placeholderToken(value);
        const leaf=String(parts[parts.length-1]||'');
        const technicalPath=/(?:file|path|url|href|filename|target|image|logo|icon)$/i.test(leaf);
        const purePlaceholder=/^(?:TBC|TBD|TBA|TODO|\?\?\?)$/i.test(value.trim());
        if (token && (!technicalPath || purePlaceholder)) issues.push({ source:'conference.yaml', kind:'config', path:parts.map((part,index)=>typeof part==='number'?'['+part+']':(index?'.':'')+part).join(''), pathParts:parts.slice(), value, token });
        return;
      }
      if (Array.isArray(value)) { value.forEach((item,index)=>walk(item, parts.concat(index))); return; }
      if (value && typeof value === 'object') Object.keys(value).forEach((key)=>walk(value[key], parts.concat(key)));
    }
    if (state.config) {
      const publicConfig = conferenceConfigForStorage();
      Object.keys(publicConfig).forEach((key)=>walk(publicConfig[key],[key]));
      if (state.regformConfig && typeof state.regformConfig === 'object') {
        const before = issues.length;
        walk(state.regformConfig, ['regform']);
        for (let i = before; i < issues.length; i += 1) issues[i].source = 'regform/settings.yaml';
      }
    }
    [['people',state.people],['program',state.program]].forEach(([kind,sheet])=>{
      (sheet.rows||[]).forEach((row,rowIndex)=>{
        (sheet.headers||[]).forEach((header)=>{
          const value=String(row[header]==null?'':row[header]); const token=placeholderToken(value);
          if(token) issues.push({source:fileNameFromPath(sheet.path),kind,rowIndex,header,path:(kind==='people'?'People':'Program')+' row '+(rowIndex+1)+' · '+header,value,token});
        });
      });
    });
    return issues;
  }

  function pageForSettingsSection(section) {
    return SETTINGS_PAGES.find((page)=>settingsPageSections(page).includes(section)) || null;
  }

  function pulseElement(node) {
    if (!node) return;
    node.classList.remove('check-target-pulse');
    void node.offsetWidth;
    node.classList.add('check-target-pulse');
    node.scrollIntoView({ behavior:'smooth', block:'center', inline:'nearest' });
    window.setTimeout(()=>node.classList.remove('check-target-pulse'), 2600);
  }

  function openPlaceholderIssue(issue) {
    if (!issue) return;
    if (issue.kind === 'config') {
      const section=String(issue.pathParts&&issue.pathParts[0]||'');
      if (issue.source === 'regform/settings.yaml' || section === 'regform') {
        switchView('regform'); renderRegformPage();
        window.setTimeout(()=>pulseElement(document.getElementById('editor-section-regform') || document.getElementById('editor-section-regform-mail')),40);
        return;
      }
      const page=pageForSettingsSection(section);
      if(page) state.activeSettingsPage=page.id;
      state.activeSettingsSection=section;
      switchView('settings'); renderSettings();
      window.setTimeout(()=>{
        const card=document.getElementById('editor-section-'+section); if(!card)return;
        const pathText=String(issue.path||'');
        const labels=Array.from(card.querySelectorAll('label'));
        const exact=labels.find((label)=>Array.from(label.querySelectorAll('small')).some((small)=>small.textContent===pathText));
        pulseElement(exact||card);
      },40);
      return;
    }
    const view=issue.kind==='people'?'people':'program'; switchView(view); renderSpreadsheet(view);
    window.setTimeout(()=>{
      const host=view==='people'?els.peopleEditor:els.programEditor;
      const input=host.querySelector('[data-kind="'+view+'"][data-row="'+issue.rowIndex+'"][data-col="'+state[view].headers.indexOf(issue.header)+'"]');
      pulseElement(input || host.querySelector('.sheet-scroll'));
      toast((view==='people'?'People':'Program')+' row '+(issue.rowIndex+1)+' · '+issue.header, 'success');
    },40);
  }

  function renderContentChecks() {
    const host=els.checksEditor; if(!host)return; host.replaceChildren();
    if(!state.config){host.append(div('empty-state compact','No conference loaded'));return;}
    const allIssues=collectPlaceholderIssues();
    const shell=div('checks-shell');
    const summary=div('check-summary-grid');
    const counts=new Map(); allIssues.forEach((i)=>counts.set(i.token,(counts.get(i.token)||0)+1));
    const stat=(value,label)=>{const node=div('check-stat'),strong=document.createElement('strong'),span=document.createElement('span');strong.textContent=String(value);span.textContent=label;node.append(strong,span);return node;};
    summary.append(stat(allIssues.length,'unresolved placeholders'));
    ['TBC','TBD','TBA','TODO'].forEach((token)=>summary.append(stat(counts.get(token)||0,token)));
    shell.append(summary);

    const toolbar=div('check-toolbar panel flat');
    const searchLabel=document.createElement('label');searchLabel.className='grow';searchLabel.textContent='Filter';const search=document.createElement('input');search.type='search';search.placeholder='Path, value, TBC, TODO…';searchLabel.append(search);
    const sourceLabel=document.createElement('label');sourceLabel.textContent='Source';const source=document.createElement('select');[['','All'],['conference.yaml','conference.yaml'],['regform/settings.yaml','Regform settings'],[fileNameFromPath(state.people.path),'People'],[fileNameFromPath(state.program.path),'Program']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;source.append(o);});sourceLabel.append(source);
    const prev=button('Previous','button ghost'),next=button('Next unresolved','button'),counter=div('sheet-meta','');toolbar.append(searchLabel,sourceLabel,prev,next,counter);shell.append(toolbar);
    const list=div('check-list');shell.append(list);host.append(shell);

    function filtered(){const q=search.value.trim().toLowerCase(),src=source.value;return allIssues.filter((i)=>(!src||i.source===src)&&(!q||[i.path,i.value,i.token,i.source].join(' ').toLowerCase().includes(q)));}
    function renderList(){const issues=filtered();if(state.checkCursor>=issues.length)state.checkCursor=Math.max(0,issues.length-1);counter.textContent=issues.length+' shown';list.replaceChildren();if(!issues.length){list.append(div('check-empty','No unresolved placeholders match this filter.'));return;}issues.forEach((issue,index)=>{const row=div('check-row'+(index===state.checkCursor?' active':''));const badge=div('check-token',issue.token);const copy=div('check-copy');copy.append(div('check-path',issue.path),div('check-value',issue.value),div('check-source',issue.source));const open=button('Open','button ghost');open.addEventListener('click',()=>{state.checkCursor=index;openPlaceholderIssue(issue);});row.append(badge,copy,open);list.append(row);});}
    function step(delta){const issues=filtered();if(!issues.length)return;state.checkCursor=(state.checkCursor+delta+issues.length)%issues.length;renderList();openPlaceholderIssue(issues[state.checkCursor]);}
    search.addEventListener('input',()=>{state.checkCursor=0;renderList();});source.addEventListener('change',()=>{state.checkCursor=0;renderList();});prev.addEventListener('click',()=>step(-1));next.addEventListener('click',()=>step(1));renderList();
  }

  function selectedPeople(kind) {
    const set = state.documentSelections[kind];
    return Array.from(set)
      .filter(i => Number.isInteger(i) && i >= 0 && i < state.people.rows.length)
      .sort((a,b)=>a-b)
      .map(i => Object.assign({}, state.people.rows[i], { __index:i }));
  }

  function participantPicker(kind, onChange, titleText) {
    const wrap=div('participant-picker');
    const title=div('participant-picker-title');
    title.append(div('participant-picker-title-copy',titleText||'People to export'));
    const count=div('sheet-meta'); title.append(count);
    const head=div('participant-picker-head');
    const all=button('Select all','button ghost'),none=button('None','button ghost');
    const search=document.createElement('input');search.type='search';search.placeholder='Find person…';search.className='participant-picker-search';
    head.append(all,none,search);
    const list=div('participant-picker-list');wrap.append(title,head,list);
    const checks=[];
    const updateCount=()=>{const set=state.documentSelections[kind];count.textContent=set.size+' selected for export';if(onChange)onChange(set.size);};
    state.people.rows.forEach((p,i)=>{
      const lab=document.createElement('label');lab.className='participant-check';
      const cb=document.createElement('input');cb.type='checkbox';cb.checked=state.documentSelections[kind].has(i);cb.dataset.index=String(i);
      const name=[p['First Name'],p['Last Name']].filter(Boolean).join(' ')||('Person '+(i+1));
      const copy=div('participant-copy');const nameEl=div('participant-name',name);copy.append(nameEl);if(p.Role)copy.append(div('participant-role',String(p.Role)));
      cb.addEventListener('change',()=>{cb.checked?state.documentSelections[kind].add(i):state.documentSelections[kind].delete(i);updateCount();});
      lab.dataset.search=(name+' '+String(p.Role||'')+' '+String(p.Affiliation||'')).toLowerCase();lab.append(cb,copy);list.append(lab);checks.push({lab,cb,i});
    });
    const applyFilter=()=>{const q=search.value.trim().toLowerCase();checks.forEach(({lab})=>lab.classList.toggle('hidden',!!q&&!lab.dataset.search.includes(q)));};
    search.addEventListener('input',applyFilter);
    all.addEventListener('click',()=>{checks.forEach(({cb,i})=>{if(!cb.closest('.participant-check').classList.contains('hidden')){state.documentSelections[kind].add(i);cb.checked=true;}});updateCount();});
    none.addEventListener('click',()=>{checks.forEach(({cb,i})=>{if(!cb.closest('.participant-check').classList.contains('hidden')){state.documentSelections[kind].delete(i);cb.checked=false;}});updateCount();});
    updateCount();
    return wrap;
  }

  function ensureCertificateSignatureConfig() {
    if (!state.config.documents || typeof state.config.documents !== 'object') state.config.documents = {};
    if (!state.config.documents.certificates || typeof state.config.documents.certificates !== 'object') state.config.documents.certificates = {};
    const cfg = state.config.documents.certificates;
    cfg.signature_columns = Number(cfg.signature_columns) === 1 ? 1 : 2;
    if (cfg.center_logo == null) cfg.center_logo = getConfig('assets.logo','');
    if (cfg.stamp_logo == null) cfg.stamp_logo = '';
    if (!Number.isFinite(Number(cfg.center_logo_width_mm))) cfg.center_logo_width_mm = 44;
    if (!Number.isFinite(Number(cfg.center_logo_offset_x_mm))) cfg.center_logo_offset_x_mm = 0;
    if (!Number.isFinite(Number(cfg.center_logo_offset_y_mm))) cfg.center_logo_offset_y_mm = 0;
    if (!Number.isFinite(Number(cfg.page_margin_mm))) cfg.page_margin_mm = 26;
    cfg.page_margin_mm = Math.max(20,Math.min(36,Number(cfg.page_margin_mm)||26));
    if (!Array.isArray(cfg.signatures)) cfg.signatures = [];
    if (!cfg.signatures.length) cfg.signatures.push({title:'Conference Chairman',name:'',affiliation:''},{title:'Scientific Chairman',name:'',affiliation:''});
    cfg.signatures = cfg.signatures.map(item => item && typeof item === 'object' ? item : {title:String(item||''),name:'',affiliation:''});
    return cfg;
  }

  function ensureBadgeDocumentConfig() {
    if(!state.config.documents||typeof state.config.documents!=='object')state.config.documents={};
    if(!state.config.documents.badges||typeof state.config.documents.badges!=='object')state.config.documents.badges={};
    const cfg=state.config.documents.badges;
    if(cfg.footer_logo==null)cfg.footer_logo='';
    if(!Array.isArray(cfg.bottom_logos))cfg.bottom_logos=cfg.footer_logo?[String(cfg.footer_logo)]:[];
    cfg.bottom_logos=cfg.bottom_logos.slice(0,3).map(value=>String(value||''));
    if(!Number.isFinite(Number(cfg.bottom_logo_count)))cfg.bottom_logo_count=cfg.bottom_logos.filter(Boolean).length;
    cfg.bottom_logo_count=Math.max(0,Math.min(3,Math.floor(Number(cfg.bottom_logo_count)||0)));
    while(cfg.bottom_logos.length<cfg.bottom_logo_count)cfg.bottom_logos.push('');
    cfg.footer_logo=cfg.bottom_logos[0]||''; // legacy filled badge template uses the first strip logo
    return cfg;
  }

  function conferenceVisuals() {
    const shortName=getConfig('conference.acronym',getConfig('site.short_name',state.projectName||'Conference'));
    const fullName=getConfig('conference.full_name',getConfig('site.title',shortName));
    const badgeName=getConfig('site.title',state.projectName||shortName||'Conference');
    const certificateShortName=String(getConfig('site.title',shortName)||shortName||'').trim();
    let certificateFullName=String(fullName||'').trim();
    if(certificateShortName&&certificateFullName.toLowerCase().startsWith(certificateShortName.toLowerCase()))certificateFullName=certificateFullName.slice(certificateShortName.length).replace(/^[\s—–-]+/,'').trim();
    const location=[getConfig('conference.venue',''),getConfig('conference.city',''),getConfig('conference.country','')].filter(Boolean).join(', ');
    const badgeLocation=[getConfig('conference.city',''),getConfig('conference.country','')].filter(Boolean).join(', ');
    const certificateLocation=[getConfig('conference.city',''),getConfig('conference.country','')].filter(Boolean).join(' - ');
    const date=getConfig('conference.date_label',[getConfig('conference.start_date',''),getConfig('conference.end_date','')].filter(Boolean).join(' - '));
    const organizerPath=getConfig('assets.organizer_logo',''); const confPath=getConfig('assets.logo','');
    const badgeCfg=ensureBadgeDocumentConfig();
    const signatureCfg=ensureCertificateSignatureConfig();
    const signatures=signatureCfg.signatures.map(item=>({title:String(item.title||''),name:String(item.name||''),affiliation:String(item.affiliation||'')})).filter(item=>item.title||item.name||item.affiliation);
    const palette=(getConfig('appearance.palettes',[])||[]).find(p=>p&&p.id===getConfig('appearance.default_palette','mifp'))||{};
    const organizerAddress=getConfig('organizer.address',getConfig('organization.address',getConfig('mifp.address','Via Appia Nuova 31, 00040 Marino, RM - Italy')));
    const phone=getConfig('conference.phone',getConfig('organizer.phone',getConfig('organization.phone','')));
    const email=getConfig('conference.email',getConfig('organizer.email',getConfig('organization.email','')));
    const badgeBottomLogoCount=Math.max(0,Math.min(3,Number(badgeCfg.bottom_logo_count)||0));
    const badgeBottomLogoPaths=(Array.isArray(badgeCfg.bottom_logos)?badgeCfg.bottom_logos:[]).slice(0,badgeBottomLogoCount).map(value=>String(value||''));
    return {shortName,badgeName,fullName,certificateShortName,certificateFullName,location,badgeLocation,certificateLocation,date,organizerPath,confPath,badgeFooterPath:String(badgeBottomLogoPaths[0]||badgeCfg.footer_logo||''),badgeBottomLogoCount,badgeBottomLogoPaths,certificateCenterPath:String(signatureCfg.center_logo||confPath||''),certificateStampPath:String(signatureCfg.stamp_logo||''),certificateMarginMm:Number(signatureCfg.page_margin_mm)||26,certificateCenterLogoWidthMm:Number(signatureCfg.center_logo_width_mm)||44,certificateCenterLogoOffsetXmm:Number(signatureCfg.center_logo_offset_x_mm)||0,certificateCenterLogoOffsetYmm:Number(signatureCfg.center_logo_offset_y_mm)||0,signatures,signatureColumns:signatureCfg.signature_columns,accent:palette.primary||'#b5122b',organizerAddress,phone,email};
  }

  function loadImage(url) { return new Promise((resolve)=>{if(!url){resolve(null);return;}const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>resolve(null);img.src=url;}); }

  async function loadDocumentAsset(path,label) {
    const assetPath=String(path||'').trim();
    if(!assetPath)return null;
    const asset=state.assets.find((item)=>item.path===assetPath);
    if(!asset){log('warn','documents.image_missing',{label:label||'image',path:assetPath});return null;}
    // Prefer decoding the actual project Blob. This does not depend on a preview
    // object URL staying alive while the certificate preview is refreshing.
    if(typeof createImageBitmap==='function'){
      try{
        const bitmap=await createImageBitmap(asset.blob);
        if(bitmap&&bitmap.width>0&&bitmap.height>0)return bitmap;
      }catch(error){
        log('debug','documents.image_bitmap_fallback',{label:label||'image',path:assetPath,message:error&&error.message||String(error)});
      }
    }
    const image=await loadImage(assetPreviewUrl(assetPath));
    if(!image)log('warn','documents.image_decode_failed',{label:label||'image',path:assetPath,type:asset.type||''});
    return image;
  }

  async function documentVisualsWithImages(){
    const v=conferenceVisuals();
    const badgeLogoPaths=Array.isArray(v.badgeBottomLogoPaths)?v.badgeBottomLogoPaths.slice(0,3):[];
    const [organizer,conference,center,stamp,badgeBottomLogos]=await Promise.all([
      loadDocumentAsset(v.organizerPath,'organizer logo'),
      loadDocumentAsset(v.confPath,'conference logo'),
      loadDocumentAsset(v.certificateCenterPath,'certificate center logo'),
      loadDocumentAsset(v.certificateStampPath,'certificate stamp'),
      Promise.all(badgeLogoPaths.map((path,index)=>loadDocumentAsset(path,'badge bottom logo '+(index+1))))
    ]);
    v.organizerLogo=organizer;v.conferenceLogo=conference;v.badgeBottomLogos=badgeBottomLogos.filter(Boolean);v.badgeFooterLogo=v.badgeBottomLogos[0]||null;v.certificateCenterLogo=center||conference;v.certificateStamp=stamp;
    v.certificateStampReady=!v.certificateStampPath||Boolean(stamp);
    return v;
  }

  function normalizedPersonRoles(person){return String(person.Role||person.Roles||person.Category||'').split(/[;,|]/).map(v=>v.trim().toLowerCase()).filter(Boolean);}

  function presentationForPerson(person) {
    const first=String(person['First Name']||person.Name||'').trim(),last=String(person['Last Name']||person.Surname||'').trim();const full=[first,last].filter(Boolean).join(' ').toLowerCase();
    const roles=normalizedPersonRoles(person);const roleText=roles.join(' ');
    let title=String(person['Presentation Title']||person['Abstract Title']||person['Talk Title']||person['Contribution Title']||person.Abstract||'').trim();
    let type=String(person['Presentation Type']||person['Talk Type']||person['Contribution Type']||person.Format||'').trim();
    if(!title&&full){
      const aliases=[full,[last,first].filter(Boolean).join(' ').toLowerCase()].filter(Boolean);
      const row=state.program.rows.find(r=>{const speaker=String(r.Speaker||r.Presenter||r.Person||'').trim().toLowerCase();if(!speaker)return false;return aliases.some(a=>speaker.includes(a)||a.includes(speaker));});
      if(row){title=String(row.Title||row['Presentation Title']||row.Abstract||'').trim();if(!type)type=String(row.Type||row.Kind||row.Format||'').trim();}
    }
    if(!title)return null;
    if(!type){
      if(/poster/i.test(roleText))type='Poster';
      else if(/oral/i.test(roleText))type='Oral';
      else if(/keynote/i.test(roleText))type='Keynote';
      else if(/plenary/i.test(roleText))type='Plenary';
      else if(/invited/i.test(roleText))type='Invited';
      else if(/speaker|lecturer/i.test(roleText))type='Talk';
    }
    let label='Presentation';
    if(/poster/i.test(type)||/poster/i.test(roleText))label='Poster presentation';
    else if(/oral/i.test(type)||/oral/i.test(roleText))label='Oral presentation';
    else if(/keynote/i.test(type)||/keynote/i.test(roleText))label='Keynote lecture';
    else if(/plenary/i.test(type)||/plenary/i.test(roleText))label='Plenary lecture';
    else if(/invited/i.test(type)||/invited/i.test(roleText))label='Invited speaker — presentation';
    else if(/speaker|lecturer/i.test(roleText)||/speaker|lecture|talk/i.test(type))label='Speaker — presentation';
    return {title,type,label,prefix:label};
  }

  function renderDocuments() {
    const host=els.documentsEditor;host.replaceChildren();if(!state.config){host.append(div('empty-state compact','No conference loaded'));return;}
    const grid=div('documents-grid');grid.append(renderBadgePanel(),renderCertificatePanel());host.append(grid);
  }

  function createDocumentProgress(){const wrap=div('doc-progress hidden'),track=div('doc-progress-track'),bar=div('doc-progress-bar'),meta=div('doc-progress-meta');const label=div('','Ready'),pct=div('','0%');track.append(bar);meta.append(label,pct);wrap.append(track,meta);return{node:wrap,set(value,text){const n=Math.max(0,Math.min(100,Math.round(value||0)));wrap.classList.remove('hidden');bar.style.width=n+'%';pct.textContent=n+'%';label.textContent=text||'Generating…';},done(text){this.set(100,text||'Done');setTimeout(()=>wrap.classList.add('hidden'),1600);},error(text){wrap.classList.remove('hidden');wrap.classList.add('error');label.textContent=text||'Generation failed';},reset(){wrap.classList.remove('error');wrap.classList.add('hidden');bar.style.width='0%';pct.textContent='0%';label.textContent='Ready';}};}
  function nextPaint(){return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));}

  function previewPersonSelect(kind,onChange){const label=document.createElement('label');label.className='doc-preview-person';label.textContent='Preview person';const select=document.createElement('select');label.append(select);function sync(){const people=selectedPeople(kind),preferred=state.documentPreviewPerson[kind];select.replaceChildren();people.forEach(p=>{const o=document.createElement('option');o.value=String(p.__index);o.textContent=[p['First Name'],p['Last Name']].filter(Boolean).join(' ')||('Person '+(p.__index+1));select.append(o);});const valid=people.some(p=>p.__index===preferred);const idx=valid?preferred:(people[0]?people[0].__index:null);state.documentPreviewPerson[kind]=idx;if(idx!=null)select.value=String(idx);select.disabled=!people.length;}sync();select.addEventListener('change',()=>{state.documentPreviewPerson[kind]=Number(select.value);if(onChange)onChange();});return{node:label,select,sync};}

  function documentValidation(message) { const error=new Error(message); error.code='DOCUMENT_VALIDATION'; return error; }

  const DOCUMENT_DUMMY_PEOPLE = [
    ['Elena','Rossi','Speaker','Sapienza University of Rome','Italy'],
    ['Marko','Petrović','Participant','University of Belgrade','Serbia'],
    ['Sofia','Martínez','Invited Speaker','Universidad Autónoma de Madrid','Spain'],
    ['Thomas','Weber','Participant','Max Planck Institute for the Science of Light','Germany'],
    ['Claire','Dubois','Poster','Université Paris-Saclay','France'],
    ['Liam','O’Connor','Participant','Trinity College Dublin','Ireland'],
    ['Anna','Kowalska','Speaker','University of Warsaw','Poland'],
    ['Nikola','Jovanović','Participant','Institute of Physics Belgrade','Serbia'],
    ['Maria','Papadopoulou','Participant','National Technical University of Athens','Greece'],
    ['David','Novak','Speaker','Charles University','Czech Republic'],
    ['Laura','Bianchi','Participant','University of Milan','Italy'],
    ['Mikkel','Hansen','Participant','Technical University of Denmark','Denmark']
  ].map((p,index)=>({'First Name':p[0],'Last Name':p[1],Role:p[2],Category:p[2],Affiliation:p[3],Country:p[4],Visible:'true',__dummy:true,__index:index}));

  function dummyBadgePeople(count){const total=Math.max(1,Number(count)||1);return Array.from({length:total},(_,index)=>Object.assign({},DOCUMENT_DUMMY_PEOPLE[index%DOCUMENT_DUMMY_PEOPLE.length],{__dummyIndex:index}));}
  function dummyCertificatePerson(){return {'First Name':'Elena','Last Name':'Rossi',Role:'Invited Speaker',Category:'Invited Speaker',Affiliation:'Sapienza University of Rome',Country:'Italy','Presentation Title':'Coherent light–matter interactions in emerging nanostructures','Presentation Type':'Invited',Visible:'true',__dummy:true};}

  function renderBadgePanel() {
    const optsState=state.documentOptions.badges;
    if (!Number.isFinite(Number(optsState.blankCount))) optsState.blankCount=0;
    if (!Number.isFinite(Number(optsState.previewPage)) || Number(optsState.previewPage)<1) optsState.previewPage=1;
    optsState.exportScope='all';
    const panel=div('panel document-panel badge-document-panel');
    const h=document.createElement('div');h.className='document-panel-head';h.innerHTML='<div><div class="eyebrow">Portrait A7 / A6 · A4 sheets</div><h2>Badges</h2><p>Preview is automatic. Person selection below affects export only.</p></div>';panel.append(h);
    const cfg=div('doc-config-grid document-control-block badge-config-grid');
    const preset=document.createElement('label');preset.className='badge-size-field';preset.textContent='Badge size';const ps=document.createElement('select');[['74x105','A7 portrait · 74 × 105 mm'],['105x148','A6 portrait · 105 × 148 mm'],['110x130','Legacy portrait · 110 × 130 mm'],['custom','Custom portrait']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;ps.append(o);});ps.value=optsState.preset||'74x105';preset.append(ps);
    const wl=document.createElement('label');wl.className='badge-dimension-field';wl.textContent='Width (mm)';const wi=document.createElement('input');wi.type='number';wi.min='50';wi.max='150';wi.value=String(optsState.widthMm);wl.append(wi);
    const hl=document.createElement('label');hl.className='badge-dimension-field';hl.textContent='Height (mm)';const hi=document.createElement('input');hi.type='number';hi.min='80';hi.max='210';hi.value=String(optsState.heightMm);hl.append(hi);
    const ol=document.createElement('label');ol.className='badge-orientation-field';ol.textContent='A4 orientation';const os=document.createElement('select');[['auto','Auto · least white space'],['portrait','Portrait'],['landscape','Landscape']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;os.append(o);});os.value=optsState.pageOrientation||'auto';ol.append(os);
    const ml=document.createElement('label');ml.textContent='Outer margin (mm)';const mi=document.createElement('input');mi.type='number';mi.min='0';mi.max='30';mi.step='.5';mi.value=String(optsState.marginMm);ml.append(mi);
    const gl=document.createElement('label');gl.textContent='Gap (mm)';const gi=document.createElement('input');gi.type='number';gi.min='0';gi.max='20';gi.step='.5';gi.value=String(optsState.gapMm);gl.append(gi);
    const cl=document.createElement('label');cl.textContent='Crop marks';const cs=document.createElement('select');[['true','Yes'],['false','No']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;cs.append(o);});cs.value=optsState.cutLines?'true':'false';cl.append(cs);
    const blankLabel=document.createElement('label');blankLabel.textContent='Blank badges';const blankInput=document.createElement('input');blankInput.type='number';blankInput.min='0';blankInput.max='200';blankInput.step='1';blankInput.value=String(Math.max(0,Number(optsState.blankCount)||0));blankLabel.append(blankInput);
    const badgeCfg=ensureBadgeDocumentConfig();
    cfg.append(preset,wl,hl,ol,ml,gl,cl,blankLabel);panel.append(cfg);
    panel.append(badgeLogoStripEditor(badgeCfg,scheduleRefresh));

    const previewSection=div('document-section document-preview-section');
    const previewHead=div('document-section-head');previewHead.innerHTML='<div><b>Preview</b><span>Uses conference People automatically; it is independent from export selection.</span></div>';
    const pageControls=div('doc-page-controls');
    const pageLabel=document.createElement('label');pageLabel.textContent='Preview page';const pageInput=document.createElement('input');pageInput.type='number';pageInput.min='1';pageInput.step='1';pageInput.value=String(optsState.previewPage||1);pageLabel.append(pageInput);const pageMeta=div('doc-page-meta','');pageControls.append(pageLabel,pageMeta);previewHead.append(pageControls);
    const summary=div('document-summary'),preview=div('doc-preview-wrap badge-preview');const status=div('doc-preview-status hidden','Updating preview…');preview.append(status);previewSection.append(previewHead,summary,preview);panel.append(previewSection);

    const exportSection=div('document-section document-export-section');
    const exportMeta=div('document-export-meta');const picker=participantPicker('badges',updateExportMeta,'People to export');exportSection.append(picker,exportMeta);panel.append(exportSection);
    const actions=div('doc-actions');const pdf=button('Export badges PDF','button primary'),docx=button('Export badges DOCX','button'),tpl=button('Filled template DOCX','button ghost'),dummy=button('Test page · dummy names','button ghost');actions.append(pdf,docx,tpl,dummy);const progress=createDocumentProgress();panel.append(actions,progress.node);

    let refreshTimer=null;
    function options(){Object.assign(optsState,{widthMm:Number(wi.value),heightMm:Number(hi.value),marginMm:Number(mi.value),gapMm:Number(gi.value),cutLines:cs.value==='true',pageOrientation:os.value,preset:ps.value,blankCount:Math.max(0,Math.floor(Number(blankInput.value)||0)),previewPage:Math.max(1,Math.floor(Number(pageInput.value)||1)),exportScope:'all'});return Object.assign({scale:6},optsState);}
    function blankBadge(index){return {'First Name':'','Last Name':'',Role:'',Affiliation:'',Country:'',__blank:true,__blankIndex:index};}
    function previewPeople(){const rows=state.people.rows.map((p,i)=>Object.assign({},p,{__index:i})).filter(p=>normalizeBooleanString(p.Visible)!=='false');return rows.length?rows:state.people.rows.map((p,i)=>Object.assign({},p,{__index:i}));}
    function previewEntries(){const people=previewPeople();const blanks=Math.max(0,Math.floor(Number(blankInput.value)||0));if(!people.length&&!blanks)return [{'First Name':'FIRST NAME','Last Name':'LAST NAME',Role:'ROLE',Affiliation:'Affiliation',Country:'Country',__sample:true}];return people.concat(Array.from({length:blanks},(_,i)=>blankBadge(i)));}
    function exportEntries(){const people=selectedPeople('badges');const blanks=Math.max(0,Math.floor(Number(blankInput.value)||0));return people.concat(Array.from({length:blanks},(_,i)=>blankBadge(i)));}
    function clampPage(layout){const pages=Math.max(1,layout.pages||1);const page=Math.min(pages,Math.max(1,Math.floor(Number(pageInput.value)||1)));pageInput.value=String(page);pageInput.max=String(pages);optsState.previewPage=page;pageMeta.textContent='of '+pages;return page;}
    function updateExportMeta(){const count=selectedPeople('badges').length,blanks=Math.max(0,Math.floor(Number(blankInput.value)||0));exportMeta.textContent=count+' people selected'+(blanks?' + '+blanks+' blank badge'+(blanks===1?'':'s'):'')+'. Export always includes the complete selected batch.';}
    function scheduleRefresh(delay){clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,delay==null?180:delay);}
    async function refresh(){const token=++state.documentPreviewToken.badges;const all=previewEntries(),layout=window.DocumentTools.badgeLayout(options(),all.length),page=clampPage(layout);summary.replaceChildren(div('',layout.cols+' × '+layout.rows+' · '+layout.perPage+' badges / A4'),div('',humanizeKey(layout.orientation)+' · '+layout.pageWidthMm+' × '+layout.pageHeightMm+' mm'),div('',all.length+' preview item'+(all.length===1?'':'s')+' · '+Math.max(1,layout.pages)+' page'+(layout.pages===1?'':'s')));status.classList.remove('hidden');preview.classList.add('is-updating');const start=(page-1)*layout.perPage,subset=all.slice(start,start+layout.perPage);const v=await documentVisualsWithImages();if(token!==state.documentPreviewToken.badges)return;const result=window.DocumentTools.renderBadgePages(subset,options(),v);if(token!==state.documentPreviewToken.badges)return;const c=result.pages[0];if(c){const stage=documentPreviewStage(c,'Badge sheet preview','A4 '+layout.orientation+' · page '+page+' / '+Math.max(1,layout.pages));preview.querySelector('.doc-preview-stage')?.remove();preview.append(stage);}status.classList.add('hidden');preview.classList.remove('is-updating');}
    function setPreset(){if(ps.value!=='custom'){const [w,h]=ps.value.split('x');wi.value=w;hi.value=h;mi.value='0';gi.value='0';}scheduleRefresh(40);}
    ps.addEventListener('change',setPreset);[wi,hi,mi,gi,blankInput].forEach(el=>{el.addEventListener('input',()=>{updateExportMeta();scheduleRefresh();});el.addEventListener('change',()=>scheduleRefresh(40));});[cs,os].forEach(el=>el.addEventListener('change',()=>scheduleRefresh(40)));pageInput.addEventListener('change',()=>scheduleRefresh(20));pageInput.addEventListener('input',()=>{optsState.previewPage=Math.max(1,Math.floor(Number(pageInput.value)||1));});
    async function pages(report){const entries=exportEntries();if(!entries.length)throw documentValidation('Select at least one person or add a blank badge.');report(5,'Loading conference assets');await nextPaint();const v=await documentVisualsWithImages();report(18,'Rendering badge sheets');await nextPaint();const result=window.DocumentTools.renderBadgePages(entries,options(),v,(n,label)=>report(18+n*.47,label));report(67,'Badge sheets rendered');return{canvases:result.pages,suffix:''};}
    pdf.addEventListener('click',()=>documentExportButton(pdf,progress,async(report)=>{const out=await pages(report);const blob=await window.DocumentTools.buildPdfFromCanvases(out.canvases,(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-badges.pdf',blob);}));
    docx.addEventListener('click',()=>documentExportButton(docx,progress,async(report)=>{const out=await pages(report);const blob=await window.DocumentTools.buildDocxFromCanvases(out.canvases,(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-badges.docx',blob);}));
    tpl.addEventListener('click',()=>documentExportButton(tpl,progress,(report)=>downloadFilledTemplateDocs('badge',report)));
    dummy.addEventListener('click',()=>documentExportButton(dummy,progress,async(report)=>{report(8,'Building a complete dummy badge sheet');const opts=options();const layout=window.DocumentTools.badgeLayout(opts,1);const people=dummyBadgePeople(layout.perPage);await nextPaint();const v=await documentVisualsWithImages();report(30,'Rendering '+people.length+' dummy badges');const result=window.DocumentTools.renderBadgePages(people,opts,v,(n,l)=>report(30+n*.38,l));const blob=await window.DocumentTools.buildPdfFromCanvases(result.pages.slice(0,1),(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-badges-test-page.pdf',blob);log('info','documents.dummy_badge_page_exported',{count:people.length,conference:v.badgeName||v.shortName});}));
    updateExportMeta();refresh();return panel;
  }

  function certificateSignatureEditor(onChange) {
    const cfg=ensureCertificateSignatureConfig();
    const wrap=div('certificate-signatures');
    const head=div('certificate-signatures-head');
    const copy=document.createElement('div');copy.innerHTML='<b>Certificate signatures</b><span>Role, name and affiliation are printed in the historical MIFP style. No signature rule is drawn.</span>';
    const layoutLabel=document.createElement('label');layoutLabel.textContent='Per row';const layout=document.createElement('select');[['1','One'],['2','Two side by side']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;layout.append(o);});layout.value=String(cfg.signature_columns||2);layoutLabel.append(layout);
    const add=button('Add signature','button ghost');head.append(copy,layoutLabel,add);wrap.append(head);
    const list=div('certificate-signature-list');wrap.append(list);
    let commitTimer=null;
    const changed=(reason,immediate)=>{clearTimeout(commitTimer);const commit=()=>{syncStructuredYaml(reason||'documents.certificates.signatures_changed',{});if(onChange)onChange();};if(immediate)commit();else commitTimer=setTimeout(commit,320);};
    function render(){list.replaceChildren();cfg.signatures.forEach((item,index)=>{const card=div('certificate-signature-card');const title=document.createElement('label');title.textContent='Title';const ti=document.createElement('input');ti.value=item.title||'';ti.placeholder='e.g. Conference President';title.append(ti);const name=document.createElement('label');name.textContent='Name Surname';const ni=document.createElement('input');ni.value=item.name||'';ni.placeholder='Name Surname';name.append(ni);const aff=document.createElement('label');aff.textContent='Affiliation';const ai=document.createElement('input');ai.value=item.affiliation||'';ai.placeholder='Institution (optional)';aff.append(ai);const actions=div('certificate-signature-actions');const up=button('↑','button ghost'),down=button('↓','button ghost'),del=button('Delete','button ghost');up.disabled=index===0;down.disabled=index===cfg.signatures.length-1;actions.append(up,down,del);card.append(name,aff,title,actions);list.append(card);
        ti.addEventListener('input',()=>{item.title=ti.value;changed();});ni.addEventListener('input',()=>{item.name=ni.value;changed();});ai.addEventListener('input',()=>{item.affiliation=ai.value;changed();});
        up.addEventListener('click',()=>{[cfg.signatures[index-1],cfg.signatures[index]]=[cfg.signatures[index],cfg.signatures[index-1]];render();changed('documents.certificates.signatures_reordered',true);});
        down.addEventListener('click',()=>{[cfg.signatures[index+1],cfg.signatures[index]]=[cfg.signatures[index],cfg.signatures[index+1]];render();changed('documents.certificates.signatures_reordered',true);});
        del.addEventListener('click',async()=>{if(!await totemConfirm('Delete signature','Remove this signature block from the certificate?','Delete',{danger:true}))return;cfg.signatures.splice(index,1);render();changed('documents.certificates.signature_deleted',true);});
      });
      if(!cfg.signatures.length)list.append(div('empty-state compact','No signature blocks. The certificate will be exported without signatures.'));
    }
    layout.addEventListener('change',()=>{cfg.signature_columns=layout.value==='1'?1:2;changed('documents.certificates.signature_layout',true);});
    add.addEventListener('click',()=>{cfg.signatures.push({title:'',name:'',affiliation:''});render();changed('documents.certificates.signature_added',true);});
    render();return wrap;
  }

  function certificateAssetField(labelText,value,allowNone,configPath) {
    const field=div('certificate-asset-field enhanced-image-field');
    const caption=div('document-image-field-title',labelText);field.append(caption);
    const select=document.createElement('select');select.className='sr-only';select.setAttribute('aria-label',labelText);
    if(allowNone){const none=document.createElement('option');none.value='';none.textContent='None';select.append(none);}
    const imageAssets=state.assets.filter(asset=>PREVIEWABLE_EXTENSIONS.has(extensionOf(asset.path))&&!asset.path.startsWith('assets/people/')).sort((a,b)=>{const score=x=>/(?:branding|logo|stamp|seal)/i.test(x.path)?0:/assets\/misc\//i.test(x.path)?1:2;return score(a)-score(b)||a.path.localeCompare(b.path);});
    imageAssets.forEach(asset=>{const o=document.createElement('option');o.value=asset.path;o.textContent=asset.path;select.append(o);});
    if(value&&!Array.from(select.options).some(o=>o.value===value)){const o=document.createElement('option');o.value=value;o.textContent=value;select.append(o);}
    select.value=value||'';

    const picker=div('document-image-picker');
    const preview=div('document-image-picker-preview');
    const controls=div('document-image-picker-controls');
    const pathText=div('yaml-image-path');
    const actions=div('document-image-picker-actions');
    const choose=button(select.value?'Change image':'Choose image','button ghost');
    const upload=button(select.value?'Replace':'Upload','button ghost');
    const view=button('View','button ghost');
    const remove=button('Remove','button ghost danger');
    actions.append(choose,upload,view,remove);controls.append(pathText,actions);picker.append(preview,controls);field.append(select,picker);

    const refreshVisual=()=>{
      preview.replaceChildren();const path=select.value||'';pathText.textContent=path||'No image selected';
      const asset=state.assets.find(a=>a.path===path);if(asset){const img=document.createElement('img');img.src=assetPreviewUrl(path);img.alt=labelText;preview.append(img);}else preview.append(div('document-image-picker-empty',path?'Missing':'None'));
      choose.textContent=path?'Change image':'Choose image';upload.textContent=path?'Replace':'Upload';view.disabled=!path;remove.disabled=!path;
    };
    const chooseModal=()=>{
      els.modalBackdrop.classList.remove('preview-modal','people-import-modal');els.modalBackdrop.classList.add('image-picker-modal');
      els.modalTitle.textContent='Choose image · '+labelText;els.modalBody.replaceChildren();
      const top=div('yaml-image-picker-top');const search=document.createElement('input');search.type='search';search.placeholder='Search project images…';const none=button('No image','button ghost');top.append(search,none);els.modalBody.append(top);
      const grid=div('yaml-image-picker-grid');els.modalBody.append(grid);
      const render=()=>{grid.replaceChildren();const q=search.value.trim().toLowerCase();imageAssets.filter(a=>!q||a.path.toLowerCase().includes(q)).forEach(asset=>{const card=button('','yaml-image-picker-card'+(asset.path===select.value?' active':''));const img=document.createElement('img');img.src=assetPreviewUrl(asset.path);img.alt=fileNameFromPath(asset.path);const copy=div('yaml-image-picker-copy');copy.append(div('yaml-image-picker-name',fileNameFromPath(asset.path)),div('yaml-image-picker-path',asset.path));card.append(img,copy);card.addEventListener('click',()=>{select.value=asset.path;closeModal();select.dispatchEvent(new Event('change',{bubbles:true}));refreshVisual();});grid.append(card);});};
      search.addEventListener('input',render);none.addEventListener('click',()=>{select.value='';closeModal();select.dispatchEvent(new Event('change',{bubbles:true}));refreshVisual();});render();els.modalBackdrop.classList.remove('hidden');window.setTimeout(()=>search.focus(),0);
    };
    choose.addEventListener('click',chooseModal);
    view.addEventListener('click',()=>openYamlImagePreview(select.value,labelText));
    remove.addEventListener('click',()=>{select.value='';select.dispatchEvent(new Event('change',{bubbles:true}));refreshVisual();});
    upload.addEventListener('click',()=>{
      const input=document.createElement('input');input.type='file';input.accept='image/*';input.hidden=true;document.body.append(input);
      input.addEventListener('change',async()=>{const file=input.files&&input.files[0];input.remove();if(!file)return;try{const current=select.value||'';const directory=current&&current.includes('/')?current.slice(0,current.lastIndexOf('/')+1):'assets/branding/';const sameExt=current&&extensionOf(current)===extensionOf(file.name)&&state.assets.some(a=>a.path===current);const target=sameExt?current:directory+file.name;await writeProjectBlob(target,file);if(Array.isArray(configPath))setAtPath(state.config,configPath,target);await loadAssets();syncStructuredYaml('documents.image_uploaded',{path:(configPath||[]).join('.'),asset:target});renderDocuments();toast('Image saved: '+target,'success');}catch(error){log('error','documents.image_upload_failed',{message:error.message});toast('Could not save image: '+error.message,'error');}});input.click();
    });
    refreshVisual();return {node:field,select,refresh:refreshVisual};
  }

  function badgeLogoStripEditor(cfg,onChange) {
    const box=div('badge-logo-strip-config');
    const head=div('badge-logo-strip-head');
    const copy=document.createElement('div');copy.innerHTML='<b>Bottom logo strip</b><span>Choose 1, 2 or 3 logos. They are centered as one group in a dedicated strip at the bottom of every badge. Rendered PDF/DOCX supports all logos; the legacy filled template uses Logo 1.</span>';
    const countLabel=document.createElement('label');countLabel.textContent='Logos';const count=document.createElement('select');[['0','None'],['1','1 · centered'],['2','2 · centered'],['3','3 · centered']].forEach(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;count.append(option);});count.value=String(cfg.bottom_logo_count||0);countLabel.append(count);head.append(copy,countLabel);box.append(head);
    const fields=div('badge-logo-strip-fields');box.append(fields);
    const commit=(reason)=>{cfg.bottom_logo_count=Math.max(0,Math.min(3,Number(count.value)||0));while(cfg.bottom_logos.length<cfg.bottom_logo_count)cfg.bottom_logos.push('');cfg.bottom_logos=cfg.bottom_logos.slice(0,cfg.bottom_logo_count);cfg.footer_logo=cfg.bottom_logos[0]||'';syncStructuredYaml(reason||'documents.badges.bottom_logos',{});if(typeof onChange==='function')onChange(70);};
    function renderFields(){fields.replaceChildren();const n=Math.max(0,Math.min(3,Number(count.value)||0));for(let i=0;i<n;i++){const field=certificateAssetField('Logo '+(i+1),cfg.bottom_logos[i]||'',true,['documents','badges','bottom_logos',i]);field.node.classList.add('badge-strip-logo-field');field.select.addEventListener('change',()=>{cfg.bottom_logos[i]=field.select.value||'';commit('documents.badges.bottom_logo_'+(i+1));});fields.append(field.node);}if(!n)fields.append(div('badge-logo-strip-empty','No bottom strip.'));}
    count.addEventListener('change',()=>{const n=Math.max(0,Math.min(3,Number(count.value)||0));while(cfg.bottom_logos.length<n)cfg.bottom_logos.push('');cfg.bottom_logos=cfg.bottom_logos.slice(0,n);commit('documents.badges.bottom_logo_count');renderFields();});
    renderFields();return box;
  }

  function certificateLogoPlacementEditor(cfg, onChange) {
    const box=div('certificate-logo-placement');
    const title=div('certificate-logo-placement-head');
    title.innerHTML='<div><b>Certificate logo position & size</b><span>Fine adjustment for the logo below the signatures.</span></div>';
    const grid=div('certificate-logo-placement-grid');
    const widthLabel=document.createElement('label');widthLabel.textContent='Width (mm)';const width=document.createElement('input');width.type='number';width.min='12';width.max='100';width.step='1';width.value=String(cfg.center_logo_width_mm||44);widthLabel.append(width);
    const xLabel=document.createElement('label');xLabel.textContent='Horizontal offset (mm)';const x=document.createElement('input');x.type='number';x.min='-60';x.max='60';x.step='1';x.value=String(cfg.center_logo_offset_x_mm||0);xLabel.append(x);
    const yLabel=document.createElement('label');yLabel.textContent='Vertical offset (mm)';const y=document.createElement('input');y.type='number';y.min='-35';y.max='20';y.step='1';y.value=String(cfg.center_logo_offset_y_mm||0);yLabel.append(y);
    grid.append(widthLabel,xLabel,yLabel);
    const actions=div('certificate-logo-placement-actions');
    const smaller=button('− Smaller','button ghost'),larger=button('+ Larger','button ghost'),left=button('← 1 mm','button ghost'),right=button('1 mm →','button ghost'),up=button('↑ 1 mm','button ghost'),down=button('↓ 1 mm','button ghost'),center=button('Center on page','button');
    actions.append(smaller,larger,left,right,up,down,center);
    function apply() {
      cfg.center_logo_width_mm=Math.max(12,Math.min(100,Number(width.value)||44));
      cfg.center_logo_offset_x_mm=Math.max(-60,Math.min(60,Number(x.value)||0));
      cfg.center_logo_offset_y_mm=Math.max(-35,Math.min(20,Number(y.value)||0));
      width.value=String(cfg.center_logo_width_mm);x.value=String(cfg.center_logo_offset_x_mm);y.value=String(cfg.center_logo_offset_y_mm);
      syncStructuredYaml('documents.certificate_logo_layout',{width_mm:cfg.center_logo_width_mm,offset_x_mm:cfg.center_logo_offset_x_mm,offset_y_mm:cfg.center_logo_offset_y_mm});
      if(typeof onChange==='function')onChange(60);
    }
    [width,x,y].forEach((input)=>input.addEventListener('change',apply));
    smaller.addEventListener('click',()=>{width.value=String((Number(width.value)||44)-2);apply();});
    larger.addEventListener('click',()=>{width.value=String((Number(width.value)||44)+2);apply();});
    left.addEventListener('click',()=>{x.value=String((Number(x.value)||0)-1);apply();});
    right.addEventListener('click',()=>{x.value=String((Number(x.value)||0)+1);apply();});
    up.addEventListener('click',()=>{y.value=String((Number(y.value)||0)-1);apply();});
    down.addEventListener('click',()=>{y.value=String((Number(y.value)||0)+1);apply();});
    center.addEventListener('click',()=>{x.value='0';apply();});
    box.append(title,grid,actions);return box;
  }

  function renderCertificatePanel() {
    const optsState=state.documentOptions.certificates,cfgState=ensureCertificateSignatureConfig();
    const panel=div('panel document-panel certificate-document-panel');const h=document.createElement('div');h.className='document-panel-head';h.innerHTML='<div><div class="eyebrow">A4 · 26 mm content margins · MIFP double frame</div><h2>Certificates</h2><p>Layout based on the historical MIFP certificates; People selection affects export only.</p></div>';panel.append(h);
    const cfg=div('doc-config-grid document-control-block');
    const pl=document.createElement('label');pl.textContent='Presentation details';const ps=document.createElement('select');[['true','Include when a title is found'],['false','Attendance only']].forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;ps.append(o);});ps.value=optsState.includePresentation!==false?'true':'false';pl.append(ps);
    const centerField=certificateAssetField('Logo below signatures',cfgState.center_logo||getConfig('assets.logo',''),true,['documents','certificates','center_logo']);const stampField=certificateAssetField('Stamp · centered below right signature',cfgState.stamp_logo||'',true,['documents','certificates','stamp_logo']);
    cfg.append(pl,centerField.node,stampField.node);panel.append(cfg);
    panel.append(certificateLogoPlacementEditor(cfgState,scheduleRefresh));
    panel.append(certificateSignatureEditor(scheduleRefresh));

    const previewSection=div('document-section document-preview-section');const previewHead=div('document-section-head');previewHead.innerHTML='<div><b>Preview</b><span>Automatic sample from People. Typing no longer clears or rebuilds the preview container.</span></div>';
    const preview=div('doc-preview-wrap certificate-preview'),status=div('doc-preview-status hidden','Updating preview…');preview.append(status);previewSection.append(previewHead,preview);panel.append(previewSection);
    const exportSection=div('document-section document-export-section');const exportMeta=div('document-export-meta');const picker=participantPicker('certificates',updateExportMeta,'People to export');exportSection.append(picker,exportMeta);panel.append(exportSection);
    const actions=div('doc-actions');const pdf=button('Export certificates PDF','button primary'),docx=button('Export certificates DOCX','button'),tpl=button('Filled template DOCX','button ghost'),dummy=button('Test certificate · dummy name','button ghost');actions.append(pdf,docx,tpl,dummy);const progress=createDocumentProgress();panel.append(actions,progress.node);

    let refreshTimer=null;
    function options(){optsState.includePresentation=ps.value==='true';return{includePresentation:optsState.includePresentation,scale:6};}
    function previewPerson(){const visible=state.people.rows.find(p=>normalizeBooleanString(p.Visible)!=='false');const base=visible||state.people.rows[0]||{'First Name':'FIRST NAME','Last Name':'LAST NAME',Affiliation:'Affiliation',Country:'Country',Role:'Speaker'};return Object.assign({},base,{__presentation:presentationForPerson(base)});}
    function updateExportMeta(){const count=selectedPeople('certificates').length;exportMeta.textContent=count+' people selected. PDF, rendered DOCX and filled template export use only this selection.';}
    function scheduleRefresh(delay){clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,typeof delay==='number'?delay:220);}
    async function pages(report){const people=selectedPeople('certificates');if(!people.length)throw documentValidation('Select at least one person for export.');report(4,'Loading conference assets');await nextPaint();const v=await documentVisualsWithImages(),out=[];for(let i=0;i<people.length;i++){const p=Object.assign({},people[i],{__presentation:presentationForPerson(people[i])});const page=window.DocumentTools.renderCertificatePages([p],options(),v)[0];out.push(page);report(10+((i+1)/people.length)*58,'Rendering certificate '+(i+1)+' / '+people.length);if(i%2===0)await nextPaint();}return out;}
    async function refresh(){const token=++state.documentPreviewToken.certificates;status.classList.remove('hidden');preview.classList.add('is-updating');const person=previewPerson(),v=await documentVisualsWithImages();if(token!==state.documentPreviewToken.certificates)return;const result=window.DocumentTools.renderCertificatePages([person],options(),v);if(token!==state.documentPreviewToken.certificates)return;const name=[person['First Name'],person['Last Name']].filter(Boolean).join(' ')||'Sample person';const stage=documentPreviewStage(result[0],'Certificate preview',name);preview.querySelector('.doc-preview-stage')?.remove();preview.append(stage);status.classList.add('hidden');preview.classList.remove('is-updating');}
    function persistVisualOptions(reason){cfgState.center_logo=centerField.select.value;cfgState.stamp_logo=stampField.select.value;syncStructuredYaml(reason,{});scheduleRefresh(80);}
    ps.addEventListener('change',()=>{optsState.includePresentation=ps.value==='true';scheduleRefresh(60);});centerField.select.addEventListener('change',()=>persistVisualOptions('documents.certificates.center_logo'));stampField.select.addEventListener('change',()=>persistVisualOptions('documents.certificates.stamp_logo'));
    pdf.addEventListener('click',()=>documentExportButton(pdf,progress,async(report)=>{const canvases=await pages(report);const blob=await window.DocumentTools.buildPdfFromCanvases(canvases,(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-certificates.pdf',blob);}));
    docx.addEventListener('click',()=>documentExportButton(docx,progress,async(report)=>{const canvases=await pages(report);const blob=await window.DocumentTools.buildDocxFromCanvases(canvases,(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-certificates.docx',blob);}));
    tpl.addEventListener('click',()=>documentExportButton(tpl,progress,(report)=>downloadFilledTemplateDocs('certificate',report)));
    dummy.addEventListener('click',()=>documentExportButton(dummy,progress,async(report)=>{report(8,'Building dummy certificate');const base=dummyCertificatePerson();const person=Object.assign({},base,{__presentation:presentationForPerson(base)});const v=await documentVisualsWithImages();report(32,'Rendering dummy certificate');const canvas=window.DocumentTools.renderCertificatePages([person],options(),v)[0];const blob=await window.DocumentTools.buildPdfFromCanvases([canvas],(n,l)=>report(68+n*.31,l));downloadBlob((state.projectName||'conference')+'-certificate-test-page.pdf',blob);log('info','documents.dummy_certificate_exported',{name:person['First Name']+' '+person['Last Name']});}));
    updateExportMeta();refresh();return panel;
  }

  async function documentExportButton(btn,progress,fn){const old=btn.textContent;btn.disabled=true;progress.reset();const report=(value,label)=>progress.set(value,label);try{report(1,'Preparing '+old.toLowerCase());await nextPaint();await fn(report);progress.done('Export ready');log('info','documents.exported',{type:old});}catch(error){const validation=error&&error.code==='DOCUMENT_VALIDATION';const message=error&&error.message?error.message:String(error||'Unknown document export error');if(validation){progress.reset();log('warn','documents.export_blocked',{type:old,message});toast(message,'warning');}else{progress.error('Generation failed');log('error','documents.export_failed',{type:old,message,name:error&&error.name||'Error',stack:error&&error.stack||''});toast('Document export failed: '+message,'error');}}finally{btn.disabled=false;btn.textContent=old;}}

  async function projectAssetAsPng(path){if(!path)return null;try{const img=await loadDocumentAsset(path,'DOCX image');if(!img)return null;const iw=Number(img.naturalWidth||img.width||0),ih=Number(img.naturalHeight||img.height||0);if(!iw||!ih)return null;const c=document.createElement('canvas');const max=900,r=Math.min(1,max/Math.max(iw,ih));c.width=Math.max(1,Math.round(iw*r));c.height=Math.max(1,Math.round(ih*r));const ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(img,0,0,c.width,c.height);return await new Promise((resolve)=>c.toBlob(resolve,'image/png'));}catch(error){log('warn','documents.image_png_failed',{path,message:error&&error.message||String(error)});return null;}}

  async function loadBundledTemplate(kind){const path=kind==='badge'?'templates/badges_template.docx':'templates/certificate_template.docx';if(location.protocol==='file:')throw new Error('Filled template DOCX export needs the editor served over local HTTP. A4 DOCX/PDF export works without it.');const response=await fetch(path);if(!response.ok)throw new Error('Could not load '+path);return response.blob();}
  function templateReplacements(kind,p){const v=conferenceVisuals(),pres=presentationForPerson(p)||{},presentationLine=pres.title?(pres.label||'Presentation'):'';const sig=Array.isArray(v.signatures)?v.signatures:[];const s1=sig[0]||{},s2=sig[1]||{},s3=sig[2]||{},s4=sig[3]||{};return {DOC_SURNAME:p['Last Name']||'',DOC_NAME:p['First Name']||'',DOC_CONF_SHORT:kind==='badge'?(v.badgeName||v.shortName):v.shortName,DOC_CONF_FULL:v.fullName,DOC_LOCATION:kind==='badge'?(v.badgeLocation||''):v.location,DOC_DATE_RANGE:v.date,DOC_DATE:v.date,DOC_ROLE:String(p.Category||p.Role||'').split(/[;,|]/)[0].trim(),AFFILIATION_:p.Affiliation||'',COUNTRY_:p.Country||'',DOC_PRESENTATION_LINE:presentationLine,DOC_PRESENTED_PREFIX:presentationLine,DOC_PRESENTATION_TYPE:pres.type||'',DOC_ABSTRACT_TITLE:pres.title||'',DOC_ORGANIZER_ADDRESS:v.organizerAddress||'',DOC_PHONE:v.phone||'',DOC_EMAIL:v.email||'',DOC_SIGN_1_TITLE:s1.title||'',DOC_SIGN_1_NAME:s1.name||'',DOC_SIGN_1_AFF:s1.affiliation||'',DOC_SIGN_2_TITLE:s2.title||'',DOC_SIGN_2_NAME:s2.name||'',DOC_SIGN_2_AFF:s2.affiliation||'',DOC_SIGN_3_TITLE:s3.title||'',DOC_SIGN_3_NAME:s3.name||'',DOC_SIGN_3_AFF:s3.affiliation||'',DOC_SIGN_4_TITLE:s4.title||'',DOC_SIGN_4_NAME:s4.name||'',DOC_SIGN_4_AFF:s4.affiliation||'',DOC_CHAIR_LEFT:s1.name||'',DOC_CHAIR_LEFT_AFF:s1.affiliation||'',DOC_CHAIR_RIGHT:s2.name||'',DOC_CHAIR_RIGHT_AFF:s2.affiliation||''};}
  async function downloadFilledTemplateDocs(kind,report){const setKind=kind==='badge'?'badges':'certificates',people=selectedPeople(setKind);if(!people.length)throw documentValidation('Select at least one person for export.');report=typeof report==='function'?report:()=>{};report(6,'Loading DOCX template');const template=await loadBundledTemplate(kind),entries=[],v=conferenceVisuals(),media={};report(14,'Loading logos');const org=await projectAssetAsPng(v.organizerPath);const second=await projectAssetAsPng(kind==='certificate'?v.certificateCenterPath:v.confPath);const third=kind==='certificate'?await projectAssetAsPng(v.certificateStampPath):await projectAssetAsPng(v.badgeFooterPath);if(org)media['word/media/image1.png']=org;if(second)media['word/media/image2.png']=second;if(third)media['word/media/image3.png']=third;for(let i=0;i<people.length;i++){const p=people[i],blob=await window.DocumentTools.fillDocxTemplate(template,templateReplacements(kind,p),media),name=slugify([p['Last Name'],p['First Name']].filter(Boolean).join('-'))||'person';entries.push({path:name+'.docx',data:blob});report(18+((i+1)/people.length)*68,'Creating DOCX '+(i+1)+' / '+people.length);await nextPaint();}report(90,entries.length===1?'Preparing DOCX':'Packing DOCX archive');if(entries.length===1)downloadBlob(entries[0].path,entries[0].data);else downloadBlob((state.projectName||'conference')+'-'+setKind+'-template-docx.zip',await window.ZipLite.createBlob(entries));report(100,'DOCX ready');log('info','documents.template_docx_exported',{kind,count:entries.length});}

  function resolvePreviewProjectPath(basePath, value) {
    const raw=String(value||'').trim();
    if(!raw || raw.startsWith('#') || /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith('//')) return '';
    const clean=raw.split(/[?#]/)[0].replace(/^\/+/, '');
    const base=String(basePath||'').replace(/\\/g,'/');
    const parts=(base.includes('/')?base.slice(0,base.lastIndexOf('/')+1):'').split('/').filter(Boolean);
    clean.split('/').forEach((part)=>{if(!part||part==='.')return;if(part==='..')parts.pop();else parts.push(part);});
    return parts.join('/');
  }

  function rewritePreviewCss(text, cssPath, assetUrls) {
    return String(text||'').replace(/url\(\s*(['"]?)([^)'"\\]+)\1\s*\)/g,(whole,_q,raw)=>{
      const value=String(raw||'').trim();
      if(!value || value.startsWith('#') || /^(?:data:|blob:|https?:|\/\/)/i.test(value)) return whole;
      const path=resolvePreviewProjectPath(cssPath,value);
      const mapped=path&&assetUrls[path];
      return mapped?'url("'+mapped+'")':whole;
    });
  }

  function patchSiteRuntimeForPreview(text) {
    let out=String(text||'');
    out=out.replace("const SITE_ROOT = new URL('../', SCRIPT_URL);","const SITE_ROOT = window.__MIFP_PREVIEW_ROOT__ ? new URL(window.__MIFP_PREVIEW_ROOT__) : new URL('../', SCRIPT_URL);");
    out=out.replace(
      "  function localUrl(path) {\n    const value = str(path).trim();",
      "  function localUrl(path) {\n    const value = str(path).trim();\n    if (window.__MIFP_PREVIEW_ASSET_URLS__) {\n      const clean=value.replace(/^\\.\\//,'').replace(/^\\/+/, '').split(/[?#]/)[0];\n      if (/\\.html$/i.test(clean)) return '#mifp-preview:' + clean;\n      if (/^regform\\/?(?:index\\.php)?$/i.test(clean)) return '#mifp-preview:__regform__.html';\n      if (window.__MIFP_PREVIEW_ASSET_URLS__[clean]) return window.__MIFP_PREVIEW_ASSET_URLS__[clean];\n    }"
    );
    out=out.replace(
      "  async function fetchText(url, label) {\n    const started = performance.now();",
      "  async function fetchText(url, label) {\n    const started = performance.now();\n    if (window.__MIFP_PREVIEW_TEXT__) {\n      let pathname=''; try { pathname=decodeURIComponent(new URL(url.href || String(url)).pathname).replace(/^\\/+/, ''); } catch (_) {}\n      const keys=Object.keys(window.__MIFP_PREVIEW_TEXT__);\n      const key=keys.find((item)=>pathname===item || pathname.endsWith('/'+item));\n      if (key) { const text=window.__MIFP_PREVIEW_TEXT__[key]; log('debug','load',label+' loaded from editor preview',{bytes:text.length,path:key}); return text; }\n    }"
    );
    return out;
  }

  function previewJson(value) {
    return JSON.stringify(value).replace(/</g,'\\u003c').replace(/>/g,'\\u003e').replace(/&/g,'\\u0026').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
  }

  function previewBootstrap(assetUrls, textFiles, projectName) {
    const root='https://mifp-preview.local/'+encodeURIComponent(projectName||'conference')+'/';
    const storageBootstrap=`for(const name of ['localStorage','sessionStorage']){const values=Object.create(null);Object.defineProperty(window,name,{value:{getItem:key=>Object.prototype.hasOwnProperty.call(values,String(key))?values[String(key)]:null,setItem:(key,value)=>{values[String(key)]=String(value)},removeItem:key=>{delete values[String(key)]},clear:()=>{for(const key of Object.keys(values))delete values[key]},key:index=>Object.keys(values)[index]??null,get length(){return Object.keys(values).length}}});}`;
    return `<script>\n${storageBootstrap}\nwindow.__MIFP_PREVIEW_ROOT__=${previewJson(root)};\nwindow.__MIFP_PREVIEW_ASSET_URLS__=${previewJson(assetUrls)};\nwindow.__MIFP_PREVIEW_TEXT__=${previewJson(textFiles)};\nwindow.__MIFP_PREVIEW_PROJECT__=${previewJson(projectName||'')};\ndocument.addEventListener('click',function(event){var a=event.target&&event.target.closest?event.target.closest('a[href^="#mifp-preview:"]'):null;if(!a)return;event.preventDefault();var page=a.getAttribute('href').slice('#mifp-preview:'.length)||'index.html';if(window.parent!==window)window.parent.postMessage({type:'mifp-preview-open-page',project:window.__MIFP_PREVIEW_PROJECT__,page:page},'*');});\n</script>`;
  }

  function buildRegistrationFormPreviewUrl() {
    const registration=(state.config&&state.config.registration)||{};
    const form=registration.form||{};
    const sections=Array.isArray(form.sections)?form.sections:[];
    const conf=(state.config&&state.config.conference)||{};
    const title=escapeHtml(form.title||registration.title||'Registration Form');
    const intro=escapeHtml(form.intro||registration.intro||'');
    const event=escapeHtml(conf.acronym||state.projectName||'Conference');
    const fieldHtml=(field)=>{
      const label=escapeHtml(field.label||field.name||'Field')+(field.required?' *':'');
      const type=String(field.type||'text').toLowerCase();
      let control='';
      if(type==='textarea') control='<textarea disabled></textarea>';
      else if(type==='select') control='<select disabled><option>'+escapeHtml((Array.isArray(field.options)&&field.options[0])||'Select…')+'</option></select>';
      else if(type==='checkbox') control='<label class="check"><input type="checkbox" disabled><span>'+escapeHtml(field.label||'Accept')+'</span></label>';
      else if(type==='file') control='<input type="file" disabled>';
      else control='<input type="'+escapeHtml(['email','date','tel','number'].includes(type)?type:'text')+'" disabled>';
      return '<label class="field"><span>'+label+'</span>'+control+(field.help?'<small>'+escapeHtml(field.help)+'</small>':'')+'</label>';
    };
    const body=sections.length?sections.map((section)=>'<section><h2>'+escapeHtml(section.title||'Section')+'</h2><div class="grid">'+(Array.isArray(section.fields)?section.fields.map(fieldHtml).join(''):'')+'</div></section>').join(''):'<section><p>No registration fields are configured yet.</p></section>';
    const html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+event+' · Registration preview</title><style>body{margin:0;background:#101214;color:#e8e8e8;font:15px/1.45 Inter,system-ui,sans-serif}main{max-width:960px;margin:auto;padding:32px 20px 64px}.note{border:1px solid #494949;background:#191c1f;padding:14px 16px;margin:18px 0 28px;border-radius:8px;color:#cfd3d7}.note b{color:#fff}h1{font:700 34px/1.1 Georgia,serif;margin:6px 0}h2{font-size:17px;margin:0 0 15px}section{border-top:1px solid #34383c;padding:24px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.field{display:grid;gap:6px}.field span{font-weight:650}.field small{color:#9ca3aa}input,select,textarea{box-sizing:border-box;width:100%;padding:10px 11px;border:1px solid #454a50;background:#171a1d;color:#ddd;border-radius:5px}textarea{min-height:84px}.check{display:flex;gap:10px;align-items:flex-start}.check input{width:auto;margin-top:4px}@media(max-width:640px){.grid{grid-template-columns:1fr}h1{font-size:28px}}</style></head><body><main><div>'+event+'</div><h1>'+title+'</h1>'+(intro?'<p>'+intro+'</p>':'')+'<div class="note"><b>Registration form preview.</b> The real conference opens <code>regform/</code> and executes the PHP form on the server. The browser preview cannot execute PHP, so this in-memory preview shows the configured fields without submitting data.</div>'+body+'</main></body></html>';
    const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));
    return {url,urls:[url],html};
  }

  function previewAssetDataUrl(blob, type) {
    // Opaque sandbox frames cannot dereference URLs owned by the editor.
    // Embed bytes instead of sharing the authenticated origin.
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve(reader.result);
      reader.onerror=()=>reject(new Error('Could not read preview asset.'));
      reader.readAsDataURL(new Blob([blob],{type}));
    });
  }

  async function buildConferencePreviewUrl(pageFile) {
    const entries=await collectProjectEntriesForExport({includeDemo:true});
    const files=new Map(entries.map((entry)=>[normalizeProjectPath(entry.path),entry.data instanceof Blob?entry.data:new Blob([entry.data]) ]));
    const page=normalizeProjectPath(pageFile||'index.html');
    if(!files.has(page)) throw new Error('Preview page not found: '+page);

    const urls=[];
    const makeUrl=(blob)=>{const url=URL.createObjectURL(blob);urls.push(url);return url;};
    const assetUrls=Object.create(null);
    const textFiles=Object.create(null);

    // Binary/static assets first, so CSS and YAML-driven image references can use them.
    for(const [path,blob] of files.entries()){
      const ext=extensionOf(path);
      if(['html','htm','css','js','yaml','yml','csv'].includes(ext))continue;
      assetUrls[path]=await previewAssetDataUrl(blob,typeFromName(path));
    }
    for(const [path,blob] of files.entries()){
      const ext=extensionOf(path);
      if(['yaml','yml','csv'].includes(ext)) textFiles[path]=await blob.text();
    }

    // Rewrite CSS url(...) references to object URLs.
    for(const [path,blob] of files.entries()){
      if(extensionOf(path)!=='css')continue;
      const css=rewritePreviewCss(await blob.text(),path,assetUrls);
      assetUrls[path]=await previewAssetDataUrl(new Blob([css]),'text/css;charset=utf-8');
    }

    // Turn scripts into object URLs. site.js gets a tiny virtual-filesystem shim.
    for(const [path,blob] of files.entries()){
      if(extensionOf(path)!=='js')continue;
      let code=await blob.text();
      if(/(^|\/)site\.js$/i.test(path)) code=patchSiteRuntimeForPreview(code);
      assetUrls[path]=await previewAssetDataUrl(new Blob([code]),'text/javascript;charset=utf-8');
    }

    let html=await files.get(page).text();
    // The real site CSP is intentionally strict. Blob/object URLs used only by the
    // editor preview need a separate execution context, so remove CSP from preview.
    html=html.replace(/<meta[^>]+http-equiv=["']Content-Security-Policy["'][^>]*>\s*/ig,'');
    html=html.replace(/<link\b([^>]*?)href=(["'])([^"']+)\2([^>]*)>/ig,(whole,before,q,href,after)=>{
      const path=resolvePreviewProjectPath(page,href);const mapped=path&&assetUrls[path];
      return mapped?`<link${before}href=${q}${mapped}${q}${after}>`:whole;
    });
    html=html.replace(/<script\b([^>]*?)src=(["'])([^"']+)\2([^>]*)><\/script>/ig,(whole,before,q,src,after)=>{
      const path=resolvePreviewProjectPath(page,src);const mapped=path&&assetUrls[path];
      return mapped?`<script${before}src=${q}${mapped}${q}${after}></script>`:whole;
    });
    html=html.replace(/<a\b([^>]*?)href=(["'])([^"']+)\2([^>]*)>/ig,(whole,before,q,href,after)=>{
      const raw=String(href||'');
      if(raw.startsWith('#')||/^[a-z][a-z0-9+.-]*:/i.test(raw))return whole;
      const path=resolvePreviewProjectPath(page,raw);
      if(path&&/\.html$/i.test(path))return `<a${before}href=${q}#mifp-preview:${path}${q}${after}>`;
      if(path&&/^regform\/?(?:index\.php)?$/i.test(path))return `<a${before}href=${q}#mifp-preview:__regform__.html${q}${after}>`;
      if(path&&assetUrls[path])return `<a${before}href=${q}${assetUrls[path]}${q}${after}>`;
      return whole;
    });
    const bootstrap=previewBootstrap(assetUrls,textFiles,state.projectName);
    if(/<\/head>/i.test(html))html=html.replace(/<\/head>/i,bootstrap+'\n</head>');else html=bootstrap+html;
    const pageUrl=makeUrl(new Blob([html],{type:'text/html;charset=utf-8'}));
    return {url:pageUrl,urls,html};
  }

  async function openConferencePreview(pageFile, options) {
    if (!state.config) return;
    const requested=String(pageFile||'index.html').split(/[?#]/)[0]||'index.html';
    const oldUrls=state.previewUrls.slice();
    const reuse=options&&options.reuse&&state.previewWindow&&!state.previewWindow.closed;
    let previewWindow=state.previewWindow;
    if(!reuse){
      // Open synchronously inside the click gesture so Chromium does not treat
      // the finished in-memory preview as an unsolicited popup.
      previewWindow=window.open(new URL('preview/',document.baseURI).href,'MIFPConferencePreview');
      if(!previewWindow){toast('The browser blocked the preview window. Allow pop-ups for the editor.','error');return;}
      state.previewWindow=previewWindow;

    }
    try {
      const built=requested==='__regform__.html' ? buildRegistrationFormPreviewUrl() : await buildConferencePreviewUrl(requested);
      state.previewUrls=built.urls;
      // Transfer only preview HTML to a trusted shell. Imported scripts run
      // inside its opaque sandbox, never in the dashboard's origin.
      await new Promise((resolve,reject)=>{
        const send=()=>previewWindow.postMessage({type:'mifp-preview-render',html:built.html},location.origin);
        const ready=(event)=>{if(event.source===previewWindow&&event.origin===location.origin&&event.data?.type==='mifp-preview-ready')send();};
        window.addEventListener('message',ready);
        const retry=window.setInterval(send,250);
        const done=(event)=>{
          if(event.source!==previewWindow||event.origin!==location.origin||event.data?.type!=='mifp-preview-rendered')return;
          cleanup(); resolve();
        };
        const timeout=window.setTimeout(()=>{cleanup();reject(new Error('Preview shell unavailable; check your admin session.'));},10000);
        function cleanup(){window.clearInterval(retry);window.clearTimeout(timeout);window.removeEventListener('message',ready);window.removeEventListener('message',done);}
        window.addEventListener('message',done); send();
      });
      window.setTimeout(()=>oldUrls.forEach((url)=>{try{URL.revokeObjectURL(url);}catch(_){}}),2500);
      log('info','preview.opened',{project:state.projectName,page:requested,mode:'in-memory'});
      toast('Preview opened from the local conference data. Nothing is published.','success');
    } catch(error) {
      log('error','preview.failed',{project:state.projectName,page:requested,message:error.message});

      toast('Could not build preview: '+error.message,'error');
    }
  }

  function exportConferenceStem() {
    const raw = String(state.projectName || 'conference').trim();
    return (raw
      .replace(/[<>:"/\\|?*\u0000-\u001F]/g, '-')
      .replace(/\s+/g, '_')
      .replace(/_+/g, '_')
      .replace(/^[._ -]+|[._ -]+$/g, '')) || 'conference';
  }

  function downloadText(filename, text, type) { downloadBlob(filename, new Blob([text], { type: type || 'text/plain;charset=utf-8' })); }

  function downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = filename; link.style.display = 'none'; document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1200);
  }

  function fileNameFromPath(path) { return String(path || '').split('/').pop() || 'file'; }
  function extensionOf(path) { const name = fileNameFromPath(path); const index = name.lastIndexOf('.'); return index >= 0 ? name.slice(index + 1).toLowerCase() : ''; }
  function typeFromName(name) {
    const ext = extensionOf(name);
    const map = { svg:'image/svg+xml', png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', gif:'image/gif', webp:'image/webp', ico:'image/x-icon', pdf:'application/pdf', csv:'text/csv', json:'application/json', txt:'text/plain', docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };
    return map[ext] || 'application/octet-stream';
  }
  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return value + ' B';
    if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB';
    return (value / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function escapeHtml(value) { return String(value == null ? '' : value).replace(/[&<>\"]/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[ch] || ch)); }
  function div(className, text) { const node = document.createElement('div'); if (className) node.className = className; if (text != null) node.textContent = String(text); return node; }
  function button(text, className) { const node = document.createElement('button'); node.type = 'button'; node.className = className || 'button'; node.textContent = text; return node; }
})();
