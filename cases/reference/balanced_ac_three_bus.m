% Independently authored three-bus balanced AC test case, not field data.
% Exact phasors: Vm=[1.04,1.00,0.98], angle=[0,-0.03,-0.06] rad.
% Demand derived by terminal current summation; bus IDs are intentionally nonconsecutive.
function mpc = authored_balanced_three_bus
mpc.version = '2';
mpc.baseMVA = 100;
mpc.bus = [
10 3 0.0 0.0 0.0 0.0 1 1.04 0.0 10 1 1.2 0.8;
20 1 -27.37914620892114 18.043187622562655 0.1 0.0 1 1.0 0.0 10 1 1.2 0.8;
30 1 98.584260545261 43.614504669477135 0.0 0.2 1 1.0 0.0 10 1 1.2 0.8;
];
mpc.gen = [
10 73.74626837210353 65.53536327774626 1000 -1000 1.04 100 1 1000 -1000];
mpc.branch = [
10 20 0.02 0.08 0.02 1000.0 1000.0 1000.0 1.02 2.0 1 -90.0 90.0;
20 30 0.04 0.12 0.01 1000.0 1000.0 1000.0 0.0 0.0 1 -90.0 90.0;
10 30 0.03 0.1 0.01 1000.0 1000.0 1000.0 0.0 0.0 1 -90.0 90.0;
];
mpc.gencost = [2 0 0 2 1 0];
