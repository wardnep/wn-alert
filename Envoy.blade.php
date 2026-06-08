@servers(['prod' => ['wardnep@wn']])

@task('deploy', ['on' => 'prod'])
	cd /home/wardnep/wn-alert
	git pull
    sudo systemctl restart xau-alert
@endtask
