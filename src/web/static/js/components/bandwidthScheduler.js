/**
 * Bandwidth scheduler component for LJS.
 *
 * Manages time-based bandwidth throttling schedules:
 * add, remove, and persist via the settings API.
 */

var currentSchedules = [];

/**
 * Public UI helper for the addBandwidthSchedule workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function addBandwidthSchedule() {
    var name = document.getElementById('bw-name').value.trim() || 'Schedule';
    var start = document.getElementById('bw-start').value;
    var end = document.getElementById('bw-end').value;
    var down = parseInt(document.getElementById('bw-down').value) || null;
    var up = parseInt(document.getElementById('bw-up').value) || null;

    var days = [];
    document.querySelectorAll('input[name="bw-days"]:checked').forEach(function(cb) {
        days.push(parseInt(cb.value));
    });

    if (!start || !end) {
        toast.show('Start and end times are required', 'err');
        return;
    }

    try {
        var r = await fetch('/api/settings/bandwidth_data');
        var schedules = [];
        if (r.ok) {
            var data = await r.json();
            schedules = data.schedules || [];
        }

        schedules.push({
            name: name,
            start_time: start,
            end_time: end,
            days: days,
            max_download_kbps: down,
            max_upload_kbps: up
        });

        await APIClient.post('/api/settings/bandwidth', { bandwidth_schedules: schedules });
        toast.show('Schedule added');
        window.location.reload();
    } catch (e) {
        toast.show('Failed to add schedule', 'err');
    }
}

/**
 * Public UI helper for the removeBandwidthSchedule workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function removeBandwidthSchedule(index) {
    try {
        var r = await fetch('/api/settings/bandwidth_data');
        if (!r.ok) return;
        var data = await r.json();
        var schedules = data.schedules || [];

        schedules.splice(index, 1);

        await APIClient.post('/api/settings/bandwidth', { bandwidth_schedules: schedules });
        toast.show('Schedule removed');
        window.location.reload();
    } catch (e) {
        toast.show('Failed to remove schedule', 'err');
    }
}
