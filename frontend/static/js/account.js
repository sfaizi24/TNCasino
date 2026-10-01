// The bet history shows one week, or the futures, at a time.
// A browser can restore the select's last choice on reload, so the groups follow it from the start.
const picker = document.getElementById('historyPicker');
if (picker) {
    const showPicked = () => {
        document.querySelectorAll('[data-group]').forEach(el => {
            el.hidden = el.dataset.group !== picker.value;
        });
    };
    picker.addEventListener('change', showPicked);
    showPicked();
}

// Save appears once a name differs from what's stored.
const form = document.getElementById('profileForm');
const save = document.getElementById('profileSave');
const fields = [...form.querySelectorAll('.tnc-acct-input')];
form.addEventListener('input', () => {
    save.hidden = fields.every(field => field.value === field.defaultValue);
});
