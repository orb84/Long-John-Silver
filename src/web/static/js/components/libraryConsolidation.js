/**
 * Library consolidation component for LJS.
 *
 * Previews and applies file reorganization (move + rename)
 * to bring the library in line with naming templates.
 */

async function previewConsolidation() {
    await runConsolidation(true);
}

/**
 * Public UI helper for the applyConsolidation workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function applyConsolidation() {
    if (!(await ljsConfirm('This will move and rename existing files in your library.', { title: 'Apply Library Consolidation', confirmText: 'Apply', danger: true }))) {
        return;
    }
    await runConsolidation(false);
}

/**
 * Public UI helper for the runConsolidation workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function runConsolidation(dryRun) {
    var resultsDiv = document.getElementById('consolidation-results');
    var tableBody = document.getElementById('consolidation-table-body');
    var summaryEl = document.getElementById('consolidation-summary');
    var applyBtn = document.getElementById('apply-consolidate-btn');

    summaryEl.textContent = 'Scanning library...';
    resultsDiv.style.display = 'block';
    tableBody.innerHTML = '';
    applyBtn.style.display = 'none';

    try {
        var data = await APIClient.post('/api/library/consolidate', { dry_run: dryRun });
        var results = data.results || [];

        if (results.length === 0) {
            summaryEl.textContent = 'No changes needed. Your library is already perfectly consolidated!';
            return;
        }

        summaryEl.textContent = 'Found ' + results.length + ' files to reorganize.';
        if (dryRun) {
            applyBtn.style.display = 'inline-block';
        }

        for (const res of results) {
            var tr = document.createElement('tr');
            tr.innerHTML = '<td><span class="badge ' + (res.status === 'moved' ? 'success' : 'dim') + '">' + res.status + '</span></td>' +
                '<td title="' + res.old_path + '">' + res.old_path + '</td>' +
                '<td title="' + res.new_path + '">' + res.new_path + '</td>';
            tableBody.appendChild(tr);
        }

        if (!dryRun) {
            toast.show('Library consolidated!');
        }

    } catch (e) {
        summaryEl.textContent = 'Consolidation failed: ' + e.message;
    }
}
