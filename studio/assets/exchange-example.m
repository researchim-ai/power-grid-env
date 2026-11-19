function mpc = small_case
mpc.version = '2';
mpc.baseMVA = 1;
mpc.bus = [1 3 0 0 0 0 1 1 0 .4 1 1.1 .9; 2 1 .02 .005 0 0 1 1 0 .4 1 1.1 .9];
mpc.gen = [1 0 0 10 -10 1 1 1 10 -10];
mpc.branch = [1 2 .0625 .03125 0 .1 .1 .1 0 0 1 -360 360];
