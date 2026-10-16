-- Fictional data only. Provisioning owner runs this; app roles cannot modify demo.
insert into app.datasets (id, name, is_demo)
values ('00000000-0000-0000-0000-000000000001', 'Fictional neighbourhood shop', true)
on conflict (id) do nothing;

insert into app.sales (id, dataset_id, source_row, sale_date, product_name, quantity, unit, unit_price, amount)
values
('00000000-0000-0000-0001-000000000001', '00000000-0000-0000-0000-000000000001', 1, '2026-09-30', 'Milk', 10, 'litre', 60, 600),
('00000000-0000-0000-0001-000000000002', '00000000-0000-0000-0000-000000000001', 2, '2026-10-01', 'Milk', 12.5, 'litre', 60, 750),
('00000000-0000-0000-0001-000000000003', '00000000-0000-0000-0000-000000000001', 3, '2026-10-01', 'Potato', 8, 'kg', 30, 240),
('00000000-0000-0000-0001-000000000004', '00000000-0000-0000-0000-000000000001', 4, '2026-10-15', 'Milk', 5, 'litre', 60, 300),
('00000000-0000-0000-0001-000000000005', '00000000-0000-0000-0000-000000000001', 5, '2026-10-15', 'Notebook', 3, 'piece', null, 150),
('00000000-0000-0000-0001-000000000006', '00000000-0000-0000-0000-000000000001', 6, '2026-11-01', 'Potato', 2, 'kg', 30, 60)
on conflict (id) do nothing;
