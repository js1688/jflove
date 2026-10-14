select
	id,
	name
from
	users
where
	age > 18
	and city = 'Beijing'
order by
	id;