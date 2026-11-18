@servers(['prod' => ['wardnep@wn']])

@task('deploy', ['on' => 'prod'])
	set -e
	cd /home/wardnep/wn-alert
	git pull --ff-only
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	sudo systemctl restart xau-alert
	sudo systemctl status xau-alert --no-pager
@endtask
