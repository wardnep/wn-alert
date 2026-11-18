@servers(['prod' => ['user01@athikit -p14321'], 'local' => '127.0.0.1'])

@task('deploy', ['on' => 'prod'])
	set -e
	cd /var/www/wn-alert
	GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_deploy -o StrictHostKeyChecking=no" \
	git pull --ff-only
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	sudo systemctl restart xau-alert
	sudo systemctl status xau-alert --no-pager
@endtask
