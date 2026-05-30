(function () {
    var API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : window.location.origin;
    var HEARTBEAT_MS = 15000;
    var clientId = 'browser-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    var form = document.getElementById('transcribe-form');
    var urlInput = document.getElementById('youtube-url');
    var submitButton = document.getElementById('submit-button');
    var tracksButton = document.getElementById('tracks-button');
    var batchForm = document.getElementById('batch-form');
    var batchUrlsInput = document.getElementById('batch-urls');
    var batchSubmitButton = document.getElementById('batch-submit-button');
    var batchPauseButton = document.getElementById('batch-pause-button');
    var batchResumeButton = document.getElementById('batch-resume-button');
    var batchCancelButton = document.getElementById('batch-cancel-button');
    var batchZipDownload = document.getElementById('batch-zip-download');
    var batchClearButton = document.getElementById('batch-clear-button');
    var batchExpandPlaylists = document.getElementById('batch-expand-playlists');
    var batchStatus = document.getElementById('batch-status');
    var batchList = document.getElementById('batch-list');
    var trackSelect = document.getElementById('track-select');
    var topicSelect = document.getElementById('topic-select');
    var includeTimestamps = document.getElementById('include-timestamps');
    var includeMetadata = document.getElementById('include-metadata');
    var paragraphMode = document.getElementById('paragraph-mode');
    var generateStudyNotes = document.getElementById('generate-study-notes');
    var startSecondsInput = document.getElementById('start-seconds');
    var endSecondsInput = document.getElementById('end-seconds');
    var tracksStatus = document.getElementById('tracks-status');
    var statusBox = document.getElementById('status-box');
    var resultPanel = document.getElementById('result-panel');
    var titleEl = document.getElementById('result-title');
    var topicEl = document.getElementById('meta-topic');
    var tagsEl = document.getElementById('meta-tags');
    var channelEl = document.getElementById('meta-channel');
    var languageEl = document.getElementById('meta-language');
    var sourceEl = document.getElementById('meta-source');
    var segmentsEl = document.getElementById('meta-segments');
    var durationEl = document.getElementById('meta-duration');
    var studyNotesEl = document.getElementById('meta-study-notes');
    var fileEl = document.getElementById('meta-file');
    var transcriptOutput = document.getElementById('transcript-output');
    var downloadLink = document.getElementById('download-link');
    var copyMdButton = document.getElementById('copy-md-button');
    var libraryRefreshButton = document.getElementById('library-refresh-button');
    var studyGuideButton = document.getElementById('study-guide-button');
    var librarySearch = document.getElementById('library-search');
    var libraryTopicFilter = document.getElementById('library-topic-filter');
    var libraryTopicTrigger = document.getElementById('library-topic-trigger');
    var libraryTopicSummary = document.getElementById('library-topic-summary');
    var libraryTopicMenu = document.getElementById('library-topic-menu');
    var libraryTopicOptions = document.getElementById('library-topic-options');
    var libraryTopicClear = document.getElementById('library-topic-clear');
    var libraryChannelFilter = document.getElementById('library-channel-filter');
    var libraryLanguageFilter = document.getElementById('library-language-filter');
    var libraryTagFilter = document.getElementById('library-tag-filter');
    var libraryTagTrigger = document.getElementById('library-tag-trigger');
    var libraryTagSummary = document.getElementById('library-tag-summary');
    var libraryTagMenu = document.getElementById('library-tag-menu');
    var libraryTagOptions = document.getElementById('library-tag-options');
    var libraryTagClear = document.getElementById('library-tag-clear');
    var studyGuideProvider = document.getElementById('study-guide-provider');
    var libraryStatus = document.getElementById('library-status');
    var libraryList = document.getElementById('library-list');
    var libraryPreview = document.getElementById('library-preview');
    var libraryPreviewTitle = document.getElementById('library-preview-title');
    var libraryPreviewDownload = document.getElementById('library-preview-download');
    var libraryPreviewCopy = document.getElementById('library-preview-copy');
    var libraryPreviewSource = document.getElementById('library-preview-source');
    var libraryPreviewTopic = document.getElementById('library-preview-topic');
    var libraryPreviewTags = document.getElementById('library-preview-tags');
    var libraryPreviewChannel = document.getElementById('library-preview-channel');
    var libraryPreviewLanguage = document.getElementById('library-preview-language');
    var libraryPreviewCreated = document.getElementById('library-preview-created');
    var libraryPreviewPath = document.getElementById('library-preview-path');
    var libraryPreviewText = document.getElementById('library-preview-text');
    var studyGuidePanel = document.getElementById('study-guide-panel');
    var studyGuideTitle = document.getElementById('study-guide-title');
    var studyGuideCopy = document.getElementById('study-guide-copy');
    var studyGuideTopic = document.getElementById('study-guide-topic');
    var studyGuideSources = document.getElementById('study-guide-sources');
    var studyGuideText = document.getElementById('study-guide-text');
    var settingsOpenButton = document.getElementById('settings-open-button');
    var settingsModal = document.getElementById('settings-modal');
    var settingsForm = document.getElementById('settings-form');
    var defaultStudyGuideProvider = document.getElementById('default-study-guide-provider');
    var settingsOutputDir = document.getElementById('settings-output-dir');
    var settingsBatchLimit = document.getElementById('settings-batch-limit');
    var settingsDefaultTimestamps = document.getElementById('settings-default-timestamps');
    var settingsDefaultMetadata = document.getElementById('settings-default-metadata');
    var settingsDefaultParagraph = document.getElementById('settings-default-paragraph');
    var settingsDefaultStudyNotes = document.getElementById('settings-default-study-notes');
    var settingsExpandPlaylists = document.getElementById('settings-expand-playlists');
    var settingsProfileList = document.getElementById('settings-profile-list');
    var settingsAddModelButton = document.getElementById('settings-add-model-button');
    var settingsSaveButton = document.getElementById('settings-save-button');
    var settingsStatus = document.getElementById('settings-status');
    var heartbeatTimer = null;
    var libraryEntries = [];
    var modelProfiles = [];
    var lastTracksUrl = '';
    var batchRunning = false;
    var currentBatchJobId = '';
    var MAX_BATCH_URLS = 10;
    var topicClassificationRunning = {};

    function setStatus(kind, message) {
        statusBox.className = 'status status-' + kind;
        statusBox.textContent = message;
    }

    function setTracksStatus(kind, message) {
        if (!tracksStatus) {
            return;
        }

        tracksStatus.className = 'tracks-status tracks-status-' + kind;
        tracksStatus.textContent = message;
    }

    function setBatchStatus(kind, message) {
        if (!batchStatus) {
            return;
        }

        batchStatus.className = 'batch-status batch-status-' + kind;
        batchStatus.textContent = message;
    }

    function apiUrl(path) {
        return API_BASE + path;
    }

    function openedFromDirectFile() {
        return window.location.protocol === 'file:';
    }

    async function checkHealth() {
        try {
            var response = await fetch(apiUrl('/api/health'));
            return response.ok;
        } catch (error) {
            return false;
        }
    }

    async function postJson(path, payload) {
        return fetch(apiUrl(path), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload || {})
        });
    }

    async function getJson(path) {
        var response = await fetch(apiUrl(path));
        var data = await response.json().catch(function () {
            return {};
        });

        if (!response.ok || !data.ok) {
            throw new Error(data.message || 'Request failed.');
        }

        return data;
    }

    function startHeartbeat() {
        stopHeartbeat();
        heartbeatTimer = window.setInterval(function () {
            postJson('/api/session/heartbeat', { client_id: clientId }).catch(function () {});
        }, HEARTBEAT_MS);
    }

    function stopHeartbeat() {
        if (heartbeatTimer) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    function closeSession() {
        try {
            var payload = new Blob(
                [JSON.stringify({ client_id: clientId })],
                { type: 'application/json' }
            );
            navigator.sendBeacon(apiUrl('/api/session/close'), payload);
        } catch (error) {
        }
    }

    function hideResult() {
        resultPanel.classList.add('is-hidden');
    }

    function formatTags(tags) {
        if (!Array.isArray(tags) || !tags.length) {
            return '-';
        }

        return tags.join(', ');
    }

    function formatDuration(duration) {
        var totalSeconds = Number(duration);
        if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) {
            return duration || '-';
        }

        totalSeconds = Math.round(totalSeconds);
        var hours = Math.floor(totalSeconds / 3600);
        var minutes = Math.floor((totalSeconds % 3600) / 60);
        var seconds = totalSeconds % 60;

        if (hours > 0) {
            return hours + ':' + String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
        }

        return minutes + ':' + String(seconds).padStart(2, '0');
    }

    function formatDate(value) {
        if (!value) {
            return '-';
        }

        var date = new Date(value);
        if (Number.isNaN(date.getTime())) {
            return value;
        }

        return date.toLocaleString('en-US', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    function getDownloadHref(entry, kind) {
        if (!entry || !entry.downloads || !entry.downloads[kind]) {
            return '';
        }

        return entry.downloads[kind];
    }

    function setLibraryStatus(kind, message) {
        if (!libraryStatus) {
            return;
        }

        libraryStatus.className = 'library-status library-status-' + kind;
        libraryStatus.textContent = message;
    }

    function setSettingsStatus(kind, message) {
        if (!settingsStatus) {
            return;
        }

        settingsStatus.className = 'status status-' + kind;
        settingsStatus.textContent = message;
    }

    function clearNode(node) {
        while (node && node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function resetTrackOptions(message) {
        if (!trackSelect) {
            return;
        }

        clearNode(trackSelect);

        var automaticOption = document.createElement('option');
        automaticOption.value = '';
        automaticOption.textContent = 'Automatic selection';
        trackSelect.appendChild(automaticOption);
        trackSelect.value = '';
        lastTracksUrl = '';

        if (message) {
            setTracksStatus('idle', message);
        }
    }

    function renderTrackOptions(tracks) {
        resetTrackOptions();

        if (!trackSelect || !Array.isArray(tracks)) {
            return;
        }

        tracks.forEach(function (track) {
            if (!track || !track.key) {
                return;
            }

            var option = document.createElement('option');
            option.value = track.key;
            option.textContent = track.label || [
                track.name || track.lang || track.key,
                track.source,
                track.ext
            ].filter(Boolean).join(' · ');
            trackSelect.appendChild(option);
        });
    }

    function resetTopicOptions() {
        if (!topicSelect) {
            return;
        }

        clearNode(topicSelect);

        var automaticOption = document.createElement('option');
        automaticOption.value = '';
        automaticOption.textContent = 'Automatic assignment';
        topicSelect.appendChild(automaticOption);
        topicSelect.value = '';
    }

    function renderTopicOptions(topics) {
        var currentValue = topicSelect ? topicSelect.value : '';
        resetTopicOptions();

        if (!topicSelect || !Array.isArray(topics)) {
            return;
        }

        topics.forEach(function (topic) {
            if (!topic || !topic.value) {
                return;
            }

            var option = document.createElement('option');
            option.value = topic.value;
            option.textContent = topic.label || topic.value;
            topicSelect.appendChild(option);
        });

        if (currentValue) {
            topicSelect.value = currentValue;
        }
    }

    async function loadTopicOptions() {
        if (!topicSelect) {
            return;
        }

        try {
            var data = await getJson('/api/topics');
            renderTopicOptions(Array.isArray(data.topics) ? data.topics : []);
        } catch (error) {
            resetTopicOptions();
        }
    }

    async function loadSettings() {
        if (!settingsForm) {
            return;
        }

        try {
            var data = await getJson('/api/settings');
            applySettings(data.settings || {});
            setSettingsStatus('idle', 'Settings are stored locally on this machine.');
        } catch (error) {
            setSettingsStatus('error', 'Could not load local settings.');
        }
    }

    function providerValue(provider, profileId) {
        if (provider === 'api' && profileId) {
            return 'api:' + profileId;
        }

        return 'local';
    }

    function parseProviderValue(value) {
        value = String(value || 'local');
        if (value.indexOf('api:') === 0) {
            return {
                provider: 'api',
                profile_id: value.slice(4)
            };
        }

        return {
            provider: 'local',
            profile_id: ''
        };
    }

    function normalizeProfiles(profiles) {
        if (!Array.isArray(profiles)) {
            return [];
        }

        return profiles
            .filter(function (profile) {
                return profile && profile.id;
            })
            .map(function (profile) {
                return {
                    id: String(profile.id),
                    name: String(profile.name || 'Model profile'),
                    kind: 'openai_compatible',
                    base_url: String(profile.base_url || ''),
                    model: String(profile.model || ''),
                    api_key_set: Boolean(profile.api_key_set)
                };
            });
    }

    function makeProfileId(name) {
        var slug = String(name || 'model')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '')
            .slice(0, 42) || 'model';
        return slug + '-' + Date.now().toString(36);
    }

    function fillEngineSelect(select, selectedValue) {
        if (!select) {
            return;
        }

        clearNode(select);

        var localOption = document.createElement('option');
        localOption.value = 'local';
        localOption.textContent = 'Local heuristic';
        select.appendChild(localOption);

        modelProfiles.forEach(function (profile) {
            var option = document.createElement('option');
            option.value = providerValue('api', profile.id);
            option.textContent = 'API: ' + profile.name + (profile.model ? ' (' + profile.model + ')' : '');
            select.appendChild(option);
        });

        select.value = selectedValue;
        if (select.value !== selectedValue) {
            select.value = 'local';
        }
    }

    function refreshEngineSelects(selectedValue) {
        fillEngineSelect(defaultStudyGuideProvider, selectedValue);
        fillEngineSelect(studyGuideProvider, selectedValue);
    }

    function applySettings(settings) {
        modelProfiles = normalizeProfiles(settings.model_profiles);
        var selectedProfileId = settings.study_guide_profile_id || (modelProfiles[0] && modelProfiles[0].id) || '';
        var selectedValue = providerValue(settings.study_guide_provider || 'local', selectedProfileId);
        var defaultOptions = settings.default_options || {};
        MAX_BATCH_URLS = Number(settings.batch_limit || MAX_BATCH_URLS) || MAX_BATCH_URLS;

        if (settingsOutputDir) {
            settingsOutputDir.value = settings.output_dir || 'outputs';
        }
        if (settingsBatchLimit) {
            settingsBatchLimit.value = String(MAX_BATCH_URLS);
        }
        if (settingsDefaultTimestamps) {
            settingsDefaultTimestamps.checked = defaultOptions.include_timestamps !== false;
        }
        if (settingsDefaultMetadata) {
            settingsDefaultMetadata.checked = defaultOptions.include_metadata !== false;
        }
        if (settingsDefaultParagraph) {
            settingsDefaultParagraph.checked = Boolean(defaultOptions.paragraph_mode);
        }
        if (settingsDefaultStudyNotes) {
            settingsDefaultStudyNotes.checked = Boolean(defaultOptions.generate_study_notes);
        }
        if (settingsExpandPlaylists) {
            settingsExpandPlaylists.checked = Boolean(settings.expand_playlists);
        }
        if (batchExpandPlaylists) {
            batchExpandPlaylists.checked = Boolean(settings.expand_playlists);
        }
        if (includeTimestamps) {
            includeTimestamps.checked = defaultOptions.include_timestamps !== false;
        }
        if (includeMetadata) {
            includeMetadata.checked = defaultOptions.include_metadata !== false;
        }
        if (paragraphMode) {
            paragraphMode.checked = Boolean(defaultOptions.paragraph_mode);
        }
        if (generateStudyNotes) {
            generateStudyNotes.checked = Boolean(defaultOptions.generate_study_notes);
        }

        refreshEngineSelects(selectedValue);
        renderModelProfiles();
    }

    function buildSettingsPayload() {
        var selection = parseProviderValue(defaultStudyGuideProvider ? defaultStudyGuideProvider.value : 'local');
        return {
            study_guide_provider: selection.provider,
            study_guide_profile_id: selection.profile_id,
            model_profiles: collectModelProfiles(),
            output_dir: settingsOutputDir ? settingsOutputDir.value.trim() : 'outputs',
            batch_limit: settingsBatchLimit ? settingsBatchLimit.value : MAX_BATCH_URLS,
            default_options: {
                include_timestamps: settingsDefaultTimestamps ? settingsDefaultTimestamps.checked : true,
                include_metadata: settingsDefaultMetadata ? settingsDefaultMetadata.checked : true,
                paragraph_mode: settingsDefaultParagraph ? settingsDefaultParagraph.checked : false,
                generate_study_notes: settingsDefaultStudyNotes ? settingsDefaultStudyNotes.checked : false
            },
            expand_playlists: settingsExpandPlaylists ? settingsExpandPlaylists.checked : false
        };
    }

    function createField(labelText, inputType, value, placeholder, fieldName) {
        var field = document.createElement('div');
        field.className = 'field';

        var label = document.createElement('label');
        label.textContent = labelText;
        field.appendChild(label);

        var input = document.createElement('input');
        input.type = inputType;
        input.value = value || '';
        input.placeholder = placeholder || '';
        input.autocomplete = 'off';
        input.setAttribute('data-profile-field', fieldName);
        field.appendChild(input);

        return field;
    }

    function renderModelProfiles() {
        if (!settingsProfileList) {
            return;
        }

        clearNode(settingsProfileList);

        if (!modelProfiles.length) {
            appendText(settingsProfileList, 'div', 'settings-empty', 'No API model profiles yet. Add one if you want to use a cloud model or a local model server.');
            return;
        }

        var selectedEngine = defaultStudyGuideProvider ? defaultStudyGuideProvider.value : 'local';
        modelProfiles.forEach(function (profile) {
            var article = document.createElement('article');
            article.className = 'settings-profile';
            article.setAttribute('data-profile-id', profile.id);

            var head = document.createElement('div');
            head.className = 'settings-profile-head';
            article.appendChild(head);

            var titleWrap = document.createElement('div');
            titleWrap.className = 'settings-profile-title';
            head.appendChild(titleWrap);
            appendText(titleWrap, 'h3', '', profile.name || 'Model profile');
            if (selectedEngine === providerValue('api', profile.id)) {
                appendText(titleWrap, 'span', 'settings-active-pill', 'Active');
            }
            appendText(
                titleWrap,
                'p',
                'settings-key-state',
                profile.api_key_set ? 'API key is set locally. Leave the key field blank to keep it.' : 'API key not set.'
            );

            var actions = document.createElement('div');
            actions.className = 'settings-profile-actions';
            head.appendChild(actions);

            var editButton = document.createElement('button');
            editButton.type = 'button';
            editButton.className = 'secondary-button';
            editButton.textContent = profile.expanded || profile.focus_name ? 'Collapse' : 'Edit';
            editButton.addEventListener('click', function () {
                modelProfiles = collectModelProfiles().map(function (item) {
                    if (item.id === profile.id) {
                        item.expanded = !(profile.expanded || profile.focus_name);
                        item.focus_name = false;
                    }
                    return item;
                });
                renderModelProfiles();
            });
            actions.appendChild(editButton);

            var removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'secondary-button';
            removeButton.textContent = 'Delete';
            removeButton.addEventListener('click', function () {
                var selectedBeforeDelete = defaultStudyGuideProvider ? defaultStudyGuideProvider.value : 'local';
                modelProfiles = collectModelProfiles().filter(function (item) {
                    return item.id !== profile.id;
                });
                refreshEngineSelects(selectedBeforeDelete === providerValue('api', profile.id) ? 'local' : selectedBeforeDelete);
                renderModelProfiles();
            });
            actions.appendChild(removeButton);

            if (!(profile.expanded || profile.focus_name)) {
                settingsProfileList.appendChild(article);
                return;
            }

            var grid = document.createElement('div');
            grid.className = 'settings-profile-grid';
            var nameField = createField('Profile Name', 'text', profile.name, 'Ollama Llama 3.1', 'name');
            grid.appendChild(nameField);
            grid.appendChild(createField('API Base URL', 'url', profile.base_url, 'http://localhost:11434/v1', 'base_url'));
            grid.appendChild(createField('Model', 'text', profile.model, 'llama3.1 or gpt-4.1-mini', 'model'));
            grid.appendChild(createField('API Key', 'password', '', 'Leave blank to keep existing key', 'api_key'));
            article.appendChild(grid);

            settingsProfileList.appendChild(article);

            if (profile.focus_name) {
                var nameInput = nameField.querySelector('[data-profile-field="name"]');
                if (nameInput) {
                    nameInput.focus();
                    nameInput.select();
                }
            }
        });
    }

    function collectModelProfiles() {
        if (!settingsProfileList) {
            return modelProfiles.slice();
        }

        var profileById = {};
        modelProfiles.forEach(function (profile) {
            profileById[profile.id] = Object.assign({}, profile);
        });

        var cards = settingsProfileList.querySelectorAll('.settings-profile');
        Array.prototype.forEach.call(cards, function (card) {
            var cardId = card.getAttribute('data-profile-id') || '';
            var existing = profileById[cardId] || {};

            function fieldValue(name) {
                var input = card.querySelector('[data-profile-field="' + name + '"]');
                return input ? input.value.trim() : existing[name] || '';
            }

            profileById[cardId] = {
                id: cardId || makeProfileId(fieldValue('name')),
                name: fieldValue('name') || 'Model profile',
                kind: 'openai_compatible',
                base_url: fieldValue('base_url'),
                model: fieldValue('model'),
                api_key: fieldValue('api_key'),
                api_key_set: Boolean(existing.api_key_set),
                expanded: Boolean(existing.expanded),
                focus_name: Boolean(existing.focus_name)
            };
        });

        return modelProfiles
            .filter(function (profile) {
                return profileById[profile.id];
            })
            .map(function (profile) {
                return profileById[profile.id];
            });
    }

    function addModelProfile() {
        modelProfiles = collectModelProfiles();
        var modelNumber = modelProfiles.length + 1;
        var name = 'New Model ' + modelNumber;
        modelProfiles.push({
            id: makeProfileId(name),
            name: name,
            kind: 'openai_compatible',
            base_url: '',
            model: '',
            api_key_set: false,
            expanded: true,
            focus_name: true
        });
        refreshEngineSelects(providerValue('api', modelProfiles[modelProfiles.length - 1].id));
        renderModelProfiles();
        setSettingsStatus('idle', 'New model profile added. Fill in the profile fields and save settings.');
    }

    function openSettingsModal() {
        if (!settingsModal) {
            return;
        }

        settingsModal.classList.remove('is-hidden');
        if (defaultStudyGuideProvider) {
            defaultStudyGuideProvider.focus();
        }
    }

    function closeSettingsModal() {
        if (!settingsModal) {
            return;
        }

        settingsModal.classList.add('is-hidden');
        if (settingsOpenButton) {
            settingsOpenButton.focus();
        }
    }

    async function saveSettings(event) {
        event.preventDefault();
        setSettingsStatus('loading', 'Saving local settings...');
        if (settingsSaveButton) {
            settingsSaveButton.disabled = true;
        }

        try {
            var response = await postJson('/api/settings', buildSettingsPayload());
            var data = await response.json().catch(function () {
                return {};
            });

            if (!response.ok || !data.ok) {
                setSettingsStatus('error', data.message || 'Could not save settings.');
                return;
            }

            applySettings(data.settings || {});
            setSettingsStatus('success', 'Settings saved locally.');
        } catch (error) {
            setSettingsStatus('error', 'Could not reach the local server while saving settings.');
        } finally {
            if (settingsSaveButton) {
                settingsSaveButton.disabled = false;
            }
        }
    }

    function readOptionalSeconds(input) {
        if (!input || !input.value.trim()) {
            return null;
        }

        var value = Number(input.value);
        if (!Number.isFinite(value)) {
            return input.value.trim();
        }

        return value;
    }

    function validateTimeRange(startSeconds, endSeconds) {
        if (startSeconds !== null && typeof startSeconds !== 'number') {
            return 'Start seconds must be a number.';
        }

        if (endSeconds !== null && typeof endSeconds !== 'number') {
            return 'End seconds must be a number.';
        }

        if (startSeconds !== null && startSeconds < 0) {
            return 'Start seconds must be 0 or greater.';
        }

        if (endSeconds !== null && endSeconds < 0) {
            return 'End seconds must be 0 or greater.';
        }

        if (startSeconds !== null && endSeconds !== null && endSeconds <= startSeconds) {
            return 'End seconds must be greater than start seconds.';
        }

        return '';
    }

    function getTranscribePayload(url) {
        var startSeconds = readOptionalSeconds(startSecondsInput);
        var endSeconds = readOptionalSeconds(endSecondsInput);

        return {
            url: url,
            track_key: trackSelect ? trackSelect.value : '',
            topic_override: topicSelect ? topicSelect.value : '',
            include_timestamps: includeTimestamps ? includeTimestamps.checked : true,
            include_metadata: includeMetadata ? includeMetadata.checked : true,
            paragraph_mode: paragraphMode ? paragraphMode.checked : false,
            generate_study_notes: generateStudyNotes ? generateStudyNotes.checked : false,
            start_seconds: startSeconds,
            end_seconds: endSeconds
        };
    }

    function getBatchTranscribePayload(url) {
        var payload = getTranscribePayload(url);
        payload.track_key = '';
        return payload;
    }

    function looksLikeVideoUrl(url) {
        try {
            var parsed = new URL(url);
            var hostname = parsed.hostname.toLowerCase();
            return parsed.protocol.indexOf('http') === 0 && (
                hostname === 'youtu.be' ||
                hostname.endsWith('.youtube.com') ||
                hostname === 'youtube.com'
            );
        } catch (error) {
            return false;
        }
    }

    function parseBatchUrls() {
        var rawText = batchUrlsInput ? batchUrlsInput.value : '';
        var seen = {};
        var urls = [];
        var invalid = [];
        var candidates = rawText
            .split(/[\s,;]+/)
            .map(function (value) {
                return value.trim();
            })
            .filter(Boolean);

        candidates.forEach(function (value) {
            if (!looksLikeVideoUrl(value)) {
                invalid.push(value);
                return;
            }
            if (!seen[value]) {
                seen[value] = true;
                urls.push(value);
            }
        });

        return {
            urls: urls,
            invalid: invalid
        };
    }

    function hasTrackOptions() {
        return Boolean(
            trackSelect &&
            (trackSelect.options.length > 1 || trackSelect.value || lastTracksUrl)
        );
    }

    function appendText(parent, tagName, className, text) {
        var element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        element.textContent = text;
        parent.appendChild(element);
        return element;
    }

    function renderBatchItems(items) {
        if (!batchList) {
            return;
        }

        clearNode(batchList);
        items.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'batch-item batch-item-' + item.status;
            row.setAttribute('data-batch-index', String(item.index));
            if (item.message) {
                row.title = item.message;
            }

            appendText(row, 'div', 'batch-url', item.url);
            appendText(row, 'span', 'batch-pill', item.label);
            batchList.appendChild(row);
        });
    }

    function updateBatchItem(items, index, status, label, message) {
        items[index].status = status;
        items[index].label = label;
        items[index].message = message || '';
        renderBatchItems(items);
    }

    function setBatchControlsDisabled(disabled) {
        if (batchSubmitButton) {
            batchSubmitButton.disabled = disabled;
        }
        if (batchPauseButton) {
            batchPauseButton.disabled = !disabled;
        }
        if (batchResumeButton) {
            batchResumeButton.disabled = true;
        }
        if (batchCancelButton) {
            batchCancelButton.disabled = !disabled;
        }
        if (batchClearButton) {
            batchClearButton.disabled = disabled;
        }
        if (batchExpandPlaylists) {
            batchExpandPlaylists.disabled = disabled;
        }
        if (submitButton) {
            submitButton.disabled = disabled;
        }
        if (tracksButton) {
            tracksButton.disabled = disabled;
        }
    }

    function updateBatchActionButtons(job) {
        var status = job && job.status ? job.status : '';
        var isTerminal = ['finished', 'finished_with_errors', 'canceled', 'interrupted'].indexOf(status) !== -1;
        var isActive = batchRunning && !isTerminal;
        var isPaused = status === 'paused';

        if (batchSubmitButton) {
            batchSubmitButton.disabled = isActive;
        }
        if (batchPauseButton) {
            batchPauseButton.disabled = !isActive || isPaused;
        }
        if (batchResumeButton) {
            batchResumeButton.disabled = !isActive || !isPaused;
        }
        if (batchCancelButton) {
            batchCancelButton.disabled = !isActive;
        }
        if (batchClearButton) {
            batchClearButton.disabled = isActive;
        }
        if (batchExpandPlaylists) {
            batchExpandPlaylists.disabled = isActive;
        }
        if (batchZipDownload) {
            var hasOutputs = Number(job && job.completed || 0) > 0;
            if (hasOutputs && currentBatchJobId) {
                batchZipDownload.href = apiUrl('/api/batch/zip?id=' + encodeURIComponent(currentBatchJobId));
                batchZipDownload.classList.remove('is-hidden');
            } else {
                batchZipDownload.href = '#';
                batchZipDownload.classList.add('is-hidden');
            }
        }
    }

    function getBatchOptionsPayload() {
        var payload = getBatchTranscribePayload('');
        delete payload.url;
        delete payload.track_key;
        return payload;
    }

    function summarizeBatchJob(job) {
        var completed = Number(job.completed || 0);
        var failed = Number(job.failed || 0);
        var canceled = Number(job.canceled || 0);
        var total = Number(job.total || 0);
        if (job.status === 'finished') {
            return 'Batch finished: ' + completed + ' transcript(s) saved.';
        }
        if (job.status === 'finished_with_errors') {
            return 'Batch finished: ' + completed + ' saved, ' + failed + ' failed.';
        }
        if (job.status === 'canceled') {
            return 'Batch canceled: ' + completed + ' saved, ' + failed + ' failed, ' + canceled + ' canceled.';
        }
        if (job.status === 'interrupted') {
            return 'Batch interrupted by server restart: ' + completed + ' saved, ' + failed + ' failed, ' + canceled + ' canceled.';
        }
        if (job.status === 'paused') {
            return 'Batch paused: ' + (completed + failed + canceled) + ' of ' + total + ' handled.';
        }
        return 'Processing batch: ' + (completed + failed + canceled) + ' of ' + total + ' handled.';
    }

    function renderBatchJob(job) {
        renderBatchItems(Array.isArray(job.items) ? job.items : []);
        if (job.status === 'finished') {
            setBatchStatus('success', summarizeBatchJob(job));
        } else if (job.status === 'finished_with_errors' || job.status === 'canceled' || job.status === 'interrupted') {
            setBatchStatus(job.completed ? 'error' : 'error', summarizeBatchJob(job));
        } else {
            setBatchStatus('loading', summarizeBatchJob(job));
        }
        updateBatchActionButtons(job);
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    async function pollBatchJob(jobId) {
        while (batchRunning && currentBatchJobId === jobId) {
            var data = await getJson('/api/batch?id=' + encodeURIComponent(jobId));
            var job = data.job || {};
            renderBatchJob(job);

            var doneItem = (job.items || []).filter(function (item) {
                return item.status === 'done' && item.result;
            }).slice(-1)[0];
            if (doneItem && doneItem.result) {
                showResult(doneItem.result);
            }

            if (['finished', 'finished_with_errors', 'canceled', 'interrupted'].indexOf(job.status) !== -1) {
                batchRunning = false;
                setBatchControlsDisabled(false);
                updateBatchActionButtons(job);
                loadLibrary();
                return;
            }

            await sleep(1200);
        }
    }

    async function processBatch(event) {
        event.preventDefault();
        if (batchRunning) {
            return;
        }

        var parsed = parseBatchUrls();
        if (parsed.invalid.length) {
            setBatchStatus('error', 'Remove invalid or non-YouTube URL(s) before starting the batch.');
            renderBatchItems(parsed.invalid.map(function (url, index) {
                return {
                    index: index,
                    url: url,
                    status: 'error',
                    label: 'invalid'
                };
            }));
            return;
        }

        if (!parsed.urls.length) {
            setBatchStatus('error', 'Add at least one YouTube video URL to the batch queue.');
            return;
        }

        if (parsed.urls.length > MAX_BATCH_URLS) {
            setBatchStatus('error', 'Batch queue limit is ' + MAX_BATCH_URLS + ' URLs per run.');
            return;
        }

        var basePayload = getBatchTranscribePayload(parsed.urls[0]);
        var timeRangeError = validateTimeRange(basePayload.start_seconds, basePayload.end_seconds);
        if (timeRangeError) {
            setBatchStatus('error', timeRangeError);
            return;
        }

        var items = parsed.urls.map(function (url, index) {
            return {
                index: index,
                url: url,
                status: 'queued',
                label: 'queued'
            };
        });

        batchRunning = true;
        setBatchControlsDisabled(true);
        hideResult();
        renderBatchItems(items);
        setBatchStatus('loading', 'Creating backend batch job...');

        try {
            var response = await postJson('/api/batch', {
                urls: parsed.urls,
                options: getBatchOptionsPayload(),
                expand_playlists: batchExpandPlaylists ? batchExpandPlaylists.checked : false
            });
            var data = await response.json().catch(function () {
                return {};
            });
            if (!response.ok || !data.ok) {
                batchRunning = false;
                currentBatchJobId = '';
                setBatchControlsDisabled(false);
                setBatchStatus('error', data.message || 'Could not create the batch job.');
                return;
            }
            currentBatchJobId = data.job.id;
            renderBatchJob(data.job);
            await pollBatchJob(currentBatchJobId);
        } catch (error) {
            batchRunning = false;
            setBatchControlsDisabled(false);
            setBatchStatus('error', 'Could not reach the local server.');
        }
    }

    function uniqueSorted(values) {
        return values
            .filter(function (value, index, list) {
                return value && list.indexOf(value) === index;
            })
            .sort(function (a, b) {
                return a.localeCompare(b, 'lt');
            });
    }

    function resetFilterSelect(select, defaultLabel, values) {
        if (!select) {
            return;
        }

        var currentValue = select.value;
        clearNode(select);

        var allOption = document.createElement('option');
        allOption.value = '';
        allOption.textContent = defaultLabel;
        select.appendChild(allOption);

        values.forEach(function (value) {
            var option = document.createElement('option');
            option.value = value;
            option.textContent = value;
            select.appendChild(option);
        });

        if (values.indexOf(currentValue) !== -1) {
            select.value = currentValue;
        }
    }

    function getSelectedLibraryTopics() {
        if (!libraryTopicOptions) {
            return [];
        }

        return Array.from(libraryTopicOptions.querySelectorAll('input[type="checkbox"]:checked')).map(function (checkbox) {
            return checkbox.value;
        }).filter(Boolean);
    }

    function resetTopicCheckboxes(values) {
        if (!libraryTopicOptions) {
            return;
        }

        var selectedTopics = getSelectedLibraryTopics();
        clearNode(libraryTopicOptions);

        if (!values.length) {
            appendText(libraryTopicOptions, 'p', 'topic-filter-empty', 'No topics yet.');
            if (libraryTopicClear) {
                libraryTopicClear.disabled = true;
            }
            updateTopicFilterSummary();
            return;
        }

        values.forEach(function (value) {
            var id = 'library-topic-filter-' + value.replace(/[^a-z0-9_-]+/gi, '-');
            var label = document.createElement('label');
            label.className = 'topic-filter-option';
            label.setAttribute('for', id);

            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = id;
            checkbox.value = value;
            checkbox.checked = selectedTopics.indexOf(value) !== -1;

            var text = document.createElement('span');
            text.textContent = value;

            label.appendChild(checkbox);
            label.appendChild(text);
            libraryTopicOptions.appendChild(label);
        });

        if (libraryTopicClear) {
            libraryTopicClear.disabled = getSelectedLibraryTopics().length === 0;
        }
        updateTopicFilterSummary();
    }

    function updateTopicFilterSummary() {
        if (!libraryTopicSummary) {
            return;
        }

        var selectedTopics = getSelectedLibraryTopics();
        if (!selectedTopics.length) {
            libraryTopicSummary.textContent = 'All topics';
        } else if (selectedTopics.length === 1) {
            libraryTopicSummary.textContent = selectedTopics[0];
        } else {
            libraryTopicSummary.textContent = selectedTopics.length + ' topics selected';
        }
    }

    function setTopicMenuOpen(open) {
        if (!libraryTopicMenu || !libraryTopicTrigger) {
            return;
        }

        libraryTopicMenu.classList.toggle('is-hidden', !open);
        libraryTopicTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function topicMenuIsOpen() {
        return !!libraryTopicMenu && !libraryTopicMenu.classList.contains('is-hidden');
    }

    function getSelectedLibraryTags() {
        if (!libraryTagOptions) {
            return [];
        }

        return Array.from(libraryTagOptions.querySelectorAll('input[type="checkbox"]:checked')).map(function (checkbox) {
            return checkbox.value;
        }).filter(Boolean);
    }

    function resetTagCheckboxes(values) {
        if (!libraryTagOptions) {
            return;
        }

        var selectedTags = getSelectedLibraryTags();
        clearNode(libraryTagOptions);

        if (!values.length) {
            appendText(libraryTagOptions, 'p', 'topic-filter-empty', 'No tags yet.');
            if (libraryTagClear) {
                libraryTagClear.disabled = true;
            }
            updateTagFilterSummary();
            return;
        }

        values.forEach(function (value) {
            var id = 'library-tag-filter-' + value.replace(/[^a-z0-9_-]+/gi, '-');
            var label = document.createElement('label');
            label.className = 'topic-filter-option';
            label.setAttribute('for', id);

            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = id;
            checkbox.value = value;
            checkbox.checked = selectedTags.indexOf(value) !== -1;

            var text = document.createElement('span');
            text.textContent = value;

            label.appendChild(checkbox);
            label.appendChild(text);
            libraryTagOptions.appendChild(label);
        });

        if (libraryTagClear) {
            libraryTagClear.disabled = getSelectedLibraryTags().length === 0;
        }
        updateTagFilterSummary();
    }

    function updateTagFilterSummary() {
        if (!libraryTagSummary) {
            return;
        }

        var selectedTags = getSelectedLibraryTags();
        if (!selectedTags.length) {
            libraryTagSummary.textContent = 'All tags';
        } else if (selectedTags.length === 1) {
            libraryTagSummary.textContent = selectedTags[0];
        } else {
            libraryTagSummary.textContent = selectedTags.length + ' tags selected';
        }
    }

    function setTagMenuOpen(open) {
        if (!libraryTagMenu || !libraryTagTrigger) {
            return;
        }

        libraryTagMenu.classList.toggle('is-hidden', !open);
        libraryTagTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function tagMenuIsOpen() {
        return !!libraryTagMenu && !libraryTagMenu.classList.contains('is-hidden');
    }

    function updateLibraryFilters() {
        resetTopicCheckboxes(
            uniqueSorted(libraryEntries.map(function (entry) {
                return entry.topic || '';
            }))
        );
        resetFilterSelect(
            libraryChannelFilter,
            'All channels',
            uniqueSorted(libraryEntries.map(function (entry) {
                return entry.channel || '';
            }))
        );
        resetFilterSelect(
            libraryLanguageFilter,
            'All languages',
            uniqueSorted(libraryEntries.map(function (entry) {
                return entry.language || '';
            }))
        );
        resetTagCheckboxes(
            uniqueSorted(libraryEntries.reduce(function (tags, entry) {
                if (Array.isArray(entry.tags)) {
                    return tags.concat(entry.tags);
                }
                if (entry.tags) {
                    tags.push(entry.tags);
                }
                return tags;
            }, []))
        );
    }

    function entryMatchesFilters(entry) {
        var query = (librarySearch && librarySearch.value || '').trim().toLowerCase();
        var selectedTopics = getSelectedLibraryTopics();
        var channel = libraryChannelFilter && libraryChannelFilter.value || '';
        var language = libraryLanguageFilter && libraryLanguageFilter.value || '';
        var selectedTags = getSelectedLibraryTags();

        if (selectedTopics.length && selectedTopics.indexOf(entry.topic || '') === -1) {
            return false;
        }

        if (channel && entry.channel !== channel) {
            return false;
        }

        if (language && entry.language !== language) {
            return false;
        }

        if (selectedTags.length) {
            var tags = Array.isArray(entry.tags) ? entry.tags : [entry.tags];
            if (!tags.some(function (tag) {
                return selectedTags.indexOf(tag) !== -1;
            })) {
                return false;
            }
        }

        if (!query) {
            return true;
        }

        var searchable = [
            entry.title,
            entry.channel,
            entry.topic,
            entry.language,
            Array.isArray(entry.tags) ? entry.tags.join(' ') : entry.tags
        ].filter(Boolean).join(' ').toLowerCase();

        return searchable.indexOf(query) !== -1;
    }

    function createDownloadLinks(entry) {
        var links = document.createElement('div');
        links.className = 'library-downloads';

        ['md', 'txt', 'json', 'srt', 'vtt'].forEach(function (kind) {
            var href = getDownloadHref(entry, kind);
            if (!href) {
                return;
            }

            var link = document.createElement('a');
            link.href = href;
            link.textContent = kind.toUpperCase();
            link.setAttribute('download', '');
            links.appendChild(link);
        });

        return links;
    }

    function createSourceLink(entry) {
        if (!entry || !entry.url) {
            return null;
        }

        var link = document.createElement('a');
        link.href = entry.url;
        link.textContent = 'YouTube';
        link.className = 'secondary-button';
        link.target = '_blank';
        link.rel = 'noreferrer';
        return link;
    }

    function createLibraryEntry(entry) {
        var article = document.createElement('article');
        article.className = 'library-entry';

        var body = document.createElement('div');
        body.className = 'library-entry-body';
        article.appendChild(body);

        appendText(body, 'h3', '', entry.title || 'Untitled');

        var meta = appendText(body, 'p', 'library-entry-meta', [
            entry.channel || 'Unknown channel',
            entry.topic || 'no topic',
            entry.topic_source ? 'topic: ' + entry.topic_source : '',
            formatDuration(entry.duration_seconds),
            entry.language || '-',
            formatDate(entry.created_at)
        ].filter(Boolean).join(' · '));

        if (!meta.textContent.trim()) {
            meta.textContent = '-';
        }

        if (Array.isArray(entry.tags) && entry.tags.length) {
            var tags = document.createElement('div');
            tags.className = 'tag-list';
            entry.tags.forEach(function (tag) {
                appendText(tags, 'span', 'tag', tag);
            });
            body.appendChild(tags);
        }

        var review = document.createElement('div');
        review.className = 'topic-review';
        var reviewedByAi = entry.topic_source === 'ai';
        appendText(review, 'span', reviewedByAi ? 'topic-review-pill topic-review-ai' : 'topic-review-pill topic-review-pending', reviewedByAi ? 'AI topic reviewed' : 'Not AI reviewed');
        if (entry.topic_confidence !== undefined && entry.topic_confidence !== null && entry.topic_confidence !== '') {
            var confidence = Number(entry.topic_confidence);
            if (!Number.isNaN(confidence)) {
                appendText(review, 'span', 'topic-review-detail', 'Confidence ' + Math.round(confidence * 100) + '%');
            }
        }
        if (entry.topic_provider) {
            appendText(review, 'span', 'topic-review-detail', entry.topic_provider);
        }
        body.appendChild(review);

        if (entry.summary) {
            appendText(body, 'p', 'library-summary', entry.summary);
        }

        var actions = document.createElement('div');
        actions.className = 'library-entry-actions';
        actions.appendChild(createDownloadLinks(entry));

        var sourceLink = createSourceLink(entry);
        if (sourceLink) {
            actions.appendChild(sourceLink);
        }

        var previewButton = document.createElement('button');
        previewButton.type = 'button';
        previewButton.className = 'secondary-button';
        previewButton.textContent = 'Preview';
        previewButton.disabled = !entry.path;
        previewButton.addEventListener('click', function () {
            previewLibraryEntry(entry);
        });
        actions.appendChild(previewButton);

        var classifyButton = document.createElement('button');
        classifyButton.type = 'button';
        classifyButton.className = 'secondary-button';
        classifyButton.textContent = entry.topic_source === 'ai' ? 'Reclassify Topic' : 'Classify Topic';
        classifyButton.disabled = !entry.path || !!topicClassificationRunning[entry.path];
        classifyButton.addEventListener('click', function () {
            classifyLibraryTopic(entry);
        });
        actions.appendChild(classifyButton);

        article.appendChild(actions);
        return article;
    }

    function renderLibrary() {
        if (!libraryList) {
            return;
        }

        clearNode(libraryList);

        if (!libraryEntries.length) {
            setLibraryStatus('idle', 'The library is empty. Generate your first transcript and it will appear here.');
            return;
        }

        var filtered = libraryEntries.filter(entryMatchesFilters);
        if (!filtered.length) {
            setLibraryStatus('idle', 'No entries match these filters.');
            return;
        }

        filtered.forEach(function (entry) {
            libraryList.appendChild(createLibraryEntry(entry));
        });

        var selectedTopics = getSelectedLibraryTopics();
        var topicSuffix = selectedTopics.length
            ? ' across ' + selectedTopics.length + ' selected topic' + (selectedTopics.length === 1 ? '' : 's')
            : '';
        var selectedTags = getSelectedLibraryTags();
        var tagSuffix = selectedTags.length
            ? ' with ' + selectedTags.length + ' selected tag' + (selectedTags.length === 1 ? '' : 's')
            : '';
        setLibraryStatus('success', 'Showing ' + filtered.length + ' of ' + libraryEntries.length + ' entries' + topicSuffix + tagSuffix + '.');
    }

    async function loadLibrary() {
        if (!libraryList) {
            return;
        }

        setLibraryStatus('loading', 'Loading the library index...');
        if (libraryRefreshButton) {
            libraryRefreshButton.disabled = true;
        }

        try {
            var data = await getJson('/api/library');
            libraryEntries = Array.isArray(data.entries) ? data.entries : [];
            updateLibraryFilters();
            renderLibrary();
        } catch (error) {
            libraryEntries = [];
            updateLibraryFilters();
            clearNode(libraryList);
            setLibraryStatus('idle', 'The library index is missing or empty. Generate a transcript and click Refresh.');
        } finally {
            if (libraryRefreshButton) {
                libraryRefreshButton.disabled = false;
            }
        }
    }

    async function previewLibraryEntry(entry) {
        if (!entry || !entry.path || !libraryPreviewText) {
            setLibraryStatus('error', 'This library entry does not have a Markdown path.');
            return;
        }

        setLibraryStatus('loading', 'Loading Markdown preview...');

        try {
            var data = await getJson('/api/library/file?path=' + encodeURIComponent(entry.path));
            libraryPreviewTitle.textContent = entry.title || 'Markdown Preview';
            libraryPreviewTopic.textContent = entry.topic || '-';
            libraryPreviewTags.textContent = formatTags(entry.tags);
            libraryPreviewChannel.textContent = entry.channel || '-';
            libraryPreviewLanguage.textContent = entry.language || '-';
            libraryPreviewCreated.textContent = formatDate(entry.created_at);
            libraryPreviewPath.textContent = data.path || entry.path;
            libraryPreviewText.value = data.text || '';
            libraryPreviewDownload.href = getDownloadHref(entry, 'md') || '#';
            libraryPreviewDownload.setAttribute('download', '');
            if (libraryPreviewSource) {
                if (entry.url) {
                    libraryPreviewSource.href = entry.url;
                    libraryPreviewSource.classList.remove('is-hidden');
                } else {
                    libraryPreviewSource.href = '#';
                    libraryPreviewSource.classList.add('is-hidden');
                }
            }
            libraryPreview.classList.remove('is-hidden');
            setLibraryStatus('success', 'Markdown preview loaded.');
        } catch (error) {
            setLibraryStatus('error', 'Could not open the Markdown file from the library.');
        }
    }

    async function generateStudyGuide() {
        if (!studyGuidePanel || !studyGuideText) {
            return;
        }

        var selectedTopics = getSelectedLibraryTopics();
        var topic = selectedTopics.length === 1 ? selectedTopics[0] : '';
        var engine = parseProviderValue(studyGuideProvider ? studyGuideProvider.value : 'local');
        var engineLabel = engine.provider === 'api' ? 'API model' : 'local heuristic';
        var topicLabel = selectedTopics.length ? selectedTopics.length + ' selected topic(s)' : 'the library';
        setLibraryStatus('loading', 'Generating a study guide from ' + topicLabel + ' with the ' + engineLabel + '...');
        if (studyGuideButton) {
            studyGuideButton.disabled = true;
        }

        try {
            var response = await postJson('/api/library/study-guide', {
                topic: topic,
                topics: selectedTopics,
                max_sources: 8,
                provider: engine.provider,
                profile_id: engine.profile_id
            });
            var data = await response.json().catch(function () {
                return {};
            });

            if (!response.ok || !data.ok) {
                setLibraryStatus('error', data.message || 'Could not generate a study guide from the library.');
                return;
            }

            var result = data.result || {};
            studyGuideTitle.textContent = 'Study Guide';
            studyGuideTopic.textContent = result.topic_label || result.topic || 'All topics';
            studyGuideSources.textContent = [
                result.sources_count || '0',
                result.provider_label || result.provider || 'local'
            ].filter(Boolean).join(' · ');
            studyGuideText.value = result.guide_text || '';
            studyGuidePanel.classList.remove('is-hidden');
            setLibraryStatus('success', 'Study guide generated from ' + (result.sources_count || 0) + ' source(s) with ' + (result.provider_label || engineLabel) + '.');
        } catch (error) {
            setLibraryStatus('error', 'Could not reach the local server while generating the study guide.');
        } finally {
            if (studyGuideButton) {
                studyGuideButton.disabled = false;
            }
        }
    }

    async function classifyLibraryTopic(entry) {
        if (!entry || !entry.path) {
            setLibraryStatus('error', 'This library entry does not have a Markdown path.');
            return;
        }

        var engine = parseProviderValue(studyGuideProvider ? studyGuideProvider.value : 'local');
        var engineLabel = engine.provider === 'api' ? 'the selected API model' : 'the local heuristic';
        var finalStatusKind = '';
        var finalStatusMessage = '';
        topicClassificationRunning[entry.path] = true;
        renderLibrary();
        setLibraryStatus('loading', 'Classifying topic for "' + (entry.title || 'Untitled') + '" with ' + engineLabel + '...');

        try {
            var response = await postJson('/api/library/classify-topic', {
                path: entry.path,
                provider: engine.provider,
                profile_id: engine.profile_id
            });
            var data = await response.json().catch(function () {
                return {};
            });

            if (!response.ok || !data.ok) {
                finalStatusKind = 'error';
                finalStatusMessage = data.message || 'Could not classify this topic.';
                return;
            }

            var result = data.result || {};
            var updatedEntry = result.entry || {};
            libraryEntries = libraryEntries.map(function (item) {
                return item.path === updatedEntry.path ? updatedEntry : item;
            });
            updateLibraryFilters();
            renderLibrary();
            await loadTopicOptions();

            var classification = result.classification || {};
            var confidence = typeof classification.confidence === 'number'
                ? ' · confidence ' + Math.round(classification.confidence * 100) + '%'
                : '';
            finalStatusKind = 'success';
            finalStatusMessage = 'Topic classified as ' + (updatedEntry.topic || classification.topic || 'other') + confidence + '.';
        } catch (error) {
            finalStatusKind = 'error';
            finalStatusMessage = 'Could not reach the local server while classifying this topic.';
        } finally {
            delete topicClassificationRunning[entry.path];
            renderLibrary();
            if (finalStatusKind && finalStatusMessage) {
                setLibraryStatus(finalStatusKind, finalStatusMessage);
            }
        }
    }

    function showResult(result) {
        result = result || {};
        titleEl.textContent = result.title || 'Markdown transcript is ready';
        topicEl.textContent = result.topic || '-';
        tagsEl.textContent = formatTags(result.tags);
        channelEl.textContent = result.channel || '-';
        languageEl.textContent = result.track_lang || '-';
        sourceEl.textContent = result.track_source || '-';
        segmentsEl.textContent = result.segments_count || '-';
        durationEl.textContent = formatDuration(result.duration);
        studyNotesEl.textContent = result.study_notes_generated ? 'Generated locally' : 'Off';
        fileEl.textContent = result.output_path || result.output_name || '-';
        transcriptOutput.value = result.transcript_text || '';
        downloadLink.href = result.download_url || '#';
        downloadLink.setAttribute('download', result.output_name || 'transcript.md');
        resultPanel.classList.remove('is-hidden');
    }

    async function copyTextWithFallback(text, textarea) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (error) {
            }
        }

        if (!textarea) {
            return false;
        }

        textarea.focus();
        textarea.select();

        try {
            return document.execCommand('copy');
        } catch (error) {
            return false;
        }
    }

    async function copyLibraryMarkdown() {
        var markdown = libraryPreviewText ? libraryPreviewText.value : '';
        if (!markdown) {
            setLibraryStatus('error', 'There is no Markdown text to copy.');
            return;
        }

        if (await copyTextWithFallback(markdown, libraryPreviewText)) {
            setLibraryStatus('success', 'Library Markdown copied.');
            return;
        }

        setLibraryStatus('error', 'Automatic copy failed. The text is selected so you can copy it manually.');
    }

    async function copyStudyGuide() {
        var markdown = studyGuideText ? studyGuideText.value : '';
        if (!markdown) {
            setLibraryStatus('error', 'There is no study guide text to copy.');
            return;
        }

        if (await copyTextWithFallback(markdown, studyGuideText)) {
            setLibraryStatus('success', 'Study guide copied.');
            return;
        }

        setLibraryStatus('error', 'Automatic copy failed. The text is selected so you can copy it manually.');
    }

    async function copyMarkdown() {
        var markdown = transcriptOutput.value;
        if (!markdown) {
            setStatus('error', 'There is no Markdown text to copy.');
            return;
        }

        if (await copyTextWithFallback(markdown, transcriptOutput)) {
            setStatus('success', 'Markdown transcript copied.');
            return;
        }

        setStatus('error', 'Automatic copy failed. The text is selected so you can copy it manually.');
    }

    if (!form) {
        return;
    }

    if (copyMdButton) {
        copyMdButton.addEventListener('click', copyMarkdown);
    }

    if (libraryPreviewCopy) {
        libraryPreviewCopy.addEventListener('click', copyLibraryMarkdown);
    }

    if (studyGuideButton) {
        studyGuideButton.addEventListener('click', generateStudyGuide);
    }

    if (studyGuideCopy) {
        studyGuideCopy.addEventListener('click', copyStudyGuide);
    }

    if (settingsOpenButton) {
        settingsOpenButton.addEventListener('click', openSettingsModal);
    }

    if (settingsModal) {
        settingsModal.addEventListener('click', function (event) {
            if (event.target && event.target.getAttribute('data-settings-close') === 'true') {
                closeSettingsModal();
            }
        });
    }

    if (settingsAddModelButton) {
        settingsAddModelButton.addEventListener('click', addModelProfile);
    }

    if (defaultStudyGuideProvider) {
        defaultStudyGuideProvider.addEventListener('change', function () {
            modelProfiles = collectModelProfiles();
            renderModelProfiles();
        });
    }

    if (settingsForm) {
        settingsForm.addEventListener('submit', saveSettings);
    }

    if (batchForm) {
        batchForm.addEventListener('submit', processBatch);
    }

    if (batchClearButton) {
        batchClearButton.addEventListener('click', function () {
            if (batchRunning) {
                return;
            }
            if (batchUrlsInput) {
                batchUrlsInput.value = '';
            }
            currentBatchJobId = '';
            clearNode(batchList);
            if (batchZipDownload) {
                batchZipDownload.href = '#';
                batchZipDownload.classList.add('is-hidden');
            }
            setBatchStatus('idle', 'Batch queue is idle.');
        });
    }

    if (batchPauseButton) {
        batchPauseButton.addEventListener('click', async function () {
            if (!batchRunning || !currentBatchJobId) {
                return;
            }
            setBatchStatus('loading', 'Pausing after the current video...');
            try {
                var response = await postJson('/api/batch/pause', { job_id: currentBatchJobId });
                var data = await response.json().catch(function () {
                    return {};
                });
                if (response.ok && data.ok) {
                    renderBatchJob(data.job || {});
                }
            } catch (error) {
                setBatchStatus('error', 'Could not reach the local server while pausing.');
            }
        });
    }

    if (batchResumeButton) {
        batchResumeButton.addEventListener('click', async function () {
            if (!batchRunning || !currentBatchJobId) {
                return;
            }
            setBatchStatus('loading', 'Resuming batch...');
            try {
                var response = await postJson('/api/batch/resume', { job_id: currentBatchJobId });
                var data = await response.json().catch(function () {
                    return {};
                });
                if (response.ok && data.ok) {
                    renderBatchJob(data.job || {});
                }
            } catch (error) {
                setBatchStatus('error', 'Could not reach the local server while resuming.');
            }
        });
    }

    if (batchCancelButton) {
        batchCancelButton.addEventListener('click', async function () {
            if (!batchRunning || !currentBatchJobId) {
                return;
            }
            setBatchStatus('loading', 'Canceling queued batch items...');
            try {
                var response = await postJson('/api/batch/cancel', { job_id: currentBatchJobId });
                var data = await response.json().catch(function () {
                    return {};
                });
                if (response.ok && data.ok) {
                    renderBatchJob(data.job || {});
                }
            } catch (error) {
                setBatchStatus('error', 'Could not reach the local server while canceling.');
            }
        });
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && settingsModal && !settingsModal.classList.contains('is-hidden')) {
            closeSettingsModal();
        }
        if (event.key === 'Escape' && topicMenuIsOpen()) {
            setTopicMenuOpen(false);
            if (libraryTopicTrigger) {
                libraryTopicTrigger.focus();
            }
        }
        if (event.key === 'Escape' && tagMenuIsOpen()) {
            setTagMenuOpen(false);
            if (libraryTagTrigger) {
                libraryTagTrigger.focus();
            }
        }
    });

    resetTrackOptions();
    resetTopicOptions();

    if (openedFromDirectFile()) {
        setStatus('error', 'This page was opened directly from a file. Run start-tool.bat or start-tool.ps1 so the local server can start.');
    } else {
        checkHealth().then(function (healthy) {
            if (healthy) {
                postJson('/api/session/open', { client_id: clientId }).catch(function () {});
                startHeartbeat();
                setStatus('idle', 'Server is running. Paste a YouTube URL to prepare a Markdown transcript.');
                loadTopicOptions();
                loadSettings();
                loadLibrary();
            } else {
                setStatus('error', 'The local server is not responding. Run start-tool.bat and wait a few seconds.');
            }
        });
    }

    window.addEventListener('beforeunload', closeSession);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            postJson('/api/session/heartbeat', { client_id: clientId }).catch(function () {});
        }
    });

    if (libraryRefreshButton) {
        libraryRefreshButton.addEventListener('click', loadLibrary);
    }

    if (librarySearch) {
        librarySearch.addEventListener('input', renderLibrary);
    }

    if (libraryTopicFilter) {
        libraryTopicFilter.addEventListener('change', function () {
            if (libraryTopicClear) {
                libraryTopicClear.disabled = getSelectedLibraryTopics().length === 0;
            }
            updateTopicFilterSummary();
            renderLibrary();
        });
    }

    if (libraryTopicTrigger) {
        libraryTopicTrigger.addEventListener('click', function (event) {
            event.stopPropagation();
            setTagMenuOpen(false);
            setTopicMenuOpen(!topicMenuIsOpen());
        });
    }

    if (libraryTopicMenu) {
        libraryTopicMenu.addEventListener('click', function (event) {
            event.stopPropagation();
        });
    }

    if (libraryTopicClear) {
        libraryTopicClear.addEventListener('click', function () {
            if (!libraryTopicOptions) {
                return;
            }
            Array.from(libraryTopicOptions.querySelectorAll('input[type="checkbox"]')).forEach(function (checkbox) {
                checkbox.checked = false;
            });
            libraryTopicClear.disabled = true;
            updateTopicFilterSummary();
            renderLibrary();
        });
    }

    if (libraryTagFilter) {
        libraryTagFilter.addEventListener('change', function () {
            if (libraryTagClear) {
                libraryTagClear.disabled = getSelectedLibraryTags().length === 0;
            }
            updateTagFilterSummary();
            renderLibrary();
        });
    }

    if (libraryTagTrigger) {
        libraryTagTrigger.addEventListener('click', function (event) {
            event.stopPropagation();
            setTopicMenuOpen(false);
            setTagMenuOpen(!tagMenuIsOpen());
        });
    }

    if (libraryTagMenu) {
        libraryTagMenu.addEventListener('click', function (event) {
            event.stopPropagation();
        });
    }

    if (libraryTagClear) {
        libraryTagClear.addEventListener('click', function () {
            if (!libraryTagOptions) {
                return;
            }
            Array.from(libraryTagOptions.querySelectorAll('input[type="checkbox"]')).forEach(function (checkbox) {
                checkbox.checked = false;
            });
            libraryTagClear.disabled = true;
            updateTagFilterSummary();
            renderLibrary();
        });
    }

    document.addEventListener('click', function (event) {
        if (libraryTopicFilter && topicMenuIsOpen() && !libraryTopicFilter.contains(event.target)) {
            setTopicMenuOpen(false);
        }

        if (libraryTagFilter && tagMenuIsOpen() && !libraryTagFilter.contains(event.target)) {
            setTagMenuOpen(false);
        }
    });

    if (libraryChannelFilter) {
        libraryChannelFilter.addEventListener('change', renderLibrary);
    }

    if (libraryLanguageFilter) {
        libraryLanguageFilter.addEventListener('change', renderLibrary);
    }

    if (urlInput) {
        urlInput.addEventListener('input', function () {
            if (hasTrackOptions() && urlInput.value.trim() !== lastTracksUrl) {
                resetTrackOptions('The URL changed. Caption track was reset to automatic selection.');
            }
        });
    }

    if (tracksButton) {
        tracksButton.addEventListener('click', async function () {
            var url = urlInput.value.trim();
            if (!url) {
                setTracksStatus('error', 'Paste a YouTube URL before checking captions.');
                setStatus('error', 'Paste a YouTube URL.');
                urlInput.focus();
                return;
            }

            setTracksStatus('loading', 'Checking available caption tracks...');
            tracksButton.disabled = true;

            try {
                var response = await postJson('/api/tracks', { url: url });
                var data = await response.json().catch(function () {
                    return {};
                });

                if (!response.ok || !data.ok) {
                    resetTrackOptions();
                    setTracksStatus('error', data.message || 'Could not check caption tracks.');
                    return;
                }

                var tracks = Array.isArray(data.tracks) ? data.tracks : [];
                var video = data.video || {};
                renderTrackOptions(tracks);
                lastTracksUrl = url;

                var videoLabel = [video.title, video.channel].filter(Boolean).join(' · ');
                var suffix = videoLabel ? ' ' + videoLabel : '';
                setTracksStatus('success', 'Caption tracks found: ' + tracks.length + '.' + suffix);
            } catch (error) {
                resetTrackOptions();
                setTracksStatus('error', 'Could not reach the local server while checking captions.');
            } finally {
                tracksButton.disabled = false;
            }
        });
    }

    form.addEventListener('submit', async function (event) {
        event.preventDefault();

        var url = urlInput.value.trim();
        if (!url) {
            setStatus('error', 'Paste a YouTube URL.');
            hideResult();
            urlInput.focus();
            return;
        }

        var payload = getTranscribePayload(url);
        var timeRangeError = validateTimeRange(payload.start_seconds, payload.end_seconds);
        if (timeRangeError) {
            setStatus('error', timeRangeError);
            hideResult();
            if (payload.start_seconds !== null && payload.start_seconds < 0 && startSecondsInput) {
                startSecondsInput.focus();
            } else if (endSecondsInput) {
                endSecondsInput.focus();
            }
            return;
        }

        setStatus('loading', 'Preparing the Markdown transcript. If YouTube is slow right now, this may take a few seconds.');
        hideResult();
        submitButton.disabled = true;
        if (tracksButton) {
            tracksButton.disabled = true;
        }

        try {
            var response = await postJson('/api/transcribe', payload);

            var data = await response.json();
            if (!response.ok || !data.ok) {
                setStatus('error', data.message || 'Could not transcribe this video.');
                return;
            }

            showResult(data.result);
            setStatus('success', 'Markdown transcript saved and added to the library index.');
            loadLibrary();
        } catch (error) {
            setStatus('error', 'Could not reach the local server. Run start-tool.bat and do not open index.html directly.');
        } finally {
            submitButton.disabled = false;
            if (tracksButton) {
                tracksButton.disabled = false;
            }
        }
    });
}());
