# Extension Popup Automation & Tab Bypass Guide

> Auxiliary domain sub-document for [brave-browsing](SKILL.md). Read via `view_file` when automating Chrome Extension Popups (`chrome-extension://...`).

## Failure Condition
When interacting with Chrome Extension Popups (`chrome-extension://<EXTENSION_ID>/popup.html`) running in standalone tabs or background contexts, clicking action buttons often triggers:
`⚠️ Please open <Target Page> (e.g. facebook.com/messages) first`

This occurs because `popup.js` calls `chrome.tabs.query({ active: true, currentWindow: true })`, which resolves to `popup.html` itself rather than the target webpage tab.

## Universal Tab Query Override (Boilerplate)
To bypass active-tab restrictions without modifying extension source code, inject this snippet via `evaluate_script` into the `popup.html` context BEFORE clicking action buttons:

```javascript
const originalQuery = chrome.tabs.query;
chrome.tabs.query = function(queryInfo, callback) {
    return new Promise((resolve) => {
        originalQuery.call(chrome.tabs, {}, (allTabs) => {
            const targetTab = allTabs.find(t => t.url && t.url.includes('<TARGET_DOMAIN>'));
            const res = targetTab ? [targetTab] : [];
            if (callback) callback(res);
            resolve(res);
        });
    });
};
```

## Agent Automation Workflow
1. List active browser pages via `list_pages` to locate the target page URL (e.g., Messenger, Facebook).
2. Open or switch to the extension popup URL: `chrome-extension://<EXTENSION_ID>/popup.html`.
3. Execute the Tab Query Override snippet in the popup context using `evaluate_script`.
4. Fill form inputs or click action buttons (`#btnToggle`, `#btnStart`, etc.).
5. Read output container or inspect DOM for completion.
